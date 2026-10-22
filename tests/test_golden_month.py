import csv
import json
from decimal import Decimal

import pytest

from closer.data.generator import (
    ATOME_METHOD,
    CARD_METHODS,
    CARD_SETTLEMENT_FEE_BP,
    FAILED_DELIVERY_ORDER,
    INSTALMENT_FEE_BP,
    MONTH,
    _bp_fee,
    bank_ambig_order_ids,
    bank_atome_order_ids,
    bank_card_gross_by_date,
    bank_split_order_ids,
    build_month,
    name_variant,
    write_month,
)
from closer.eval.golden import score_commission


@pytest.fixture(scope="module")
def month_files(tmp_path_factory):
    return write_month(tmp_path_factory.mktemp("golden"))


@pytest.fixture(scope="module")
def key(month_files):
    return json.loads(month_files["answer_key.json"].read_text("utf-8"))


def test_same_seed_same_bytes(tmp_path):
    a = write_month(tmp_path / "a", seed=7)
    b = write_month(tmp_path / "b", seed=7)
    for name in a:
        assert a[name].read_bytes() == b[name].read_bytes()


def test_volumes_match_the_plan():
    month = build_month()
    assert len(month["pos"]) == 95
    assert len(month["roadshow_only"]) + len(month["duplicates"]) == 30


def test_seeded_anomaly_counts(key):
    assert len(key["duplicates"]) == 5
    assert len(key["missing_salesperson"]) == 3
    unapproved = [o for o in key["golden_orders"] if o["discount_pct"] > 10 and not o["discount_approved_by"]]
    assert [o["order_id"] for o in unapproved] == ["1027"]
    assert sum(len(o["split"]) > 1 for o in key["golden_orders"]) == 3


def test_team_sale_is_shared_by_four_salespeople(key):
    team = next(o for o in key["golden_orders"] if o["order_id"] == "1020")
    assert team["split"] == {"S09": 40, "S06": 20, "S07": 20, "S08": 20}
    assert team["salesperson"] == "S09"


def test_duplicates_share_a_phone_but_not_the_typed_name(month_files, key):
    with month_files["roadshow.csv"].open(encoding="utf-8", newline="") as f:
        rows = {int(r["row"]): r for r in csv.DictReader(f)}
    pos = {o["order_id"]: o for o in json.loads(month_files["pos_orders.json"].read_text("utf-8"))}
    for dup in key["duplicates"]:
        row, order = rows[dup["roadshow_row"]], pos[dup["pos_order_id"]]
        assert row["phone"] == order["phone"]
        assert row["customer_name"] != order["customer_name"]


def test_missing_salespeople_are_blank_on_the_sheet(month_files, key):
    with month_files["roadshow.csv"].open(encoding="utf-8", newline="") as f:
        blank = {int(r["row"]) for r in csv.DictReader(f) if not r["salesperson"]}
    assert blank == {m["roadshow_row"] for m in key["missing_salesperson"]}


def test_answer_key_is_not_in_agent_visible_files(month_files):
    visible = (
        month_files["pos_orders.json"].read_text("utf-8")
        + month_files["roadshow.csv"].read_text("utf-8")
        + month_files["bank.csv"].read_text("utf-8")
        + month_files["settlements.csv"].read_text("utf-8")
        + month_files["crew_messages.json"].read_text("utf-8")
        + month_files["august_payouts.json"].read_text("utf-8")
    )
    for leak in ("delivered_on", "refunded_on", "paid_cents", "true_salesperson"):
        assert leak not in visible


def test_name_variant():
    assert name_variant("Tan Ah Kow") == "Mr Tan A.K."


def test_engine_matches_answer_key(key):
    report = score_commission(key)
    assert report["passed"], report["mismatches"]


# ---------------------------------------------------------------------------
# New file existence tests
# ---------------------------------------------------------------------------

def test_new_agent_files_exist(month_files):
    for name in ("bank.csv", "settlements.csv", "crew_messages.json", "august_payouts.json"):
        assert month_files[name].exists(), f"{name} was not written"


# ---------------------------------------------------------------------------
# Determinism tests: same seed → byte-identical new files
# ---------------------------------------------------------------------------

def test_bank_csv_deterministic(tmp_path):
    a = write_month(tmp_path / "a", seed=42)
    b = write_month(tmp_path / "b", seed=42)
    assert a["bank.csv"].read_bytes() == b["bank.csv"].read_bytes()


def test_crew_messages_deterministic(tmp_path):
    a = write_month(tmp_path / "a", seed=42)
    b = write_month(tmp_path / "b", seed=42)
    assert a["crew_messages.json"].read_bytes() == b["crew_messages.json"].read_bytes()


def test_august_payouts_deterministic(tmp_path):
    a = write_month(tmp_path / "a", seed=42)
    b = write_month(tmp_path / "b", seed=42)
    assert a["august_payouts.json"].read_bytes() == b["august_payouts.json"].read_bytes()


# ---------------------------------------------------------------------------
# bank.csv structural / semantic tests
# ---------------------------------------------------------------------------

def _read_bank(month_files) -> list[dict]:
    with month_files["bank.csv"].open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_bank_csv_columns(month_files):
    rows = _read_bank(month_files)
    assert rows, "bank.csv must not be empty"
    required = {"date", "value_date", "description", "reference", "debit", "credit", "balance"}
    assert set(rows[0].keys()) == required


def test_bank_csv_balance_runs_correctly(month_files):
    """Each balance cell must equal the previous balance + credit - debit."""
    rows = _read_bank(month_files)
    for i, row in enumerate(rows):
        debit = Decimal(row["debit"])
        credit = Decimal(row["credit"])
        balance = Decimal(row["balance"])
        if i == 0:
            # Just check it's a valid number and not negative.
            assert balance >= 0
        else:
            prev = Decimal(rows[i - 1]["balance"])
            assert prev + credit - debit == balance, (
                f"Row {i}: balance mismatch (prev={prev}, credit={credit}, debit={debit}, got={balance})"
            )


def test_bank_csv_every_paid_order_payments_sum_to_net(month_files, key):
    """
    For every fully-paid September order that has direct ORD-prefixed bank rows,
    the sum of those credit rows must equal net_total_cents.

    Excluded (no ORD DEP/BAL rows by design):
    - Visa/Mastercard orders: batched into CARD SETTLEMENT rows.
    - Atome orders: replaced by a single INSTALMENT PAYOUT credit (ORD...-INST).
    - Ambiguous-reference orders: their description/ref is overwritten.

    Split-payment orders: the split produces two ORD-referenced legs; they must
    sum to net unless one leg happened to land in a card method (≤ net is valid then).
    """
    month = build_month()
    split_ids = bank_split_order_ids(month)
    ambig_ids = bank_ambig_order_ids(month)
    atome_ids = bank_atome_order_ids(month)

    # Accumulate credits per order_id from ORD-prefixed, non-REF, non-INST refs.
    rows = _read_bank(month_files)
    credits_by_order: dict[str, Decimal] = {}
    for row in rows:
        ref = row["reference"]
        if ref.startswith("ORD") and not ref.endswith("-REF") and not ref.endswith("-INST"):
            oid = ref[3:]  # strips "ORD"; preserves "RS-01" etc.
            credits_by_order[oid] = credits_by_order.get(oid, Decimal("0")) + Decimal(row["credit"])

    # Target: non-card, non-Atome, non-ambiguous, fully paid in September.
    target_orders = [
        o for o in key["golden_orders"]
        if o.get("paid_on") and o["paid_on"].startswith(MONTH)
        and o["payment_method"] not in CARD_METHODS
        and o["payment_method"] != ATOME_METHOD
        and o["order_id"] not in ambig_ids
    ]
    assert target_orders, "expected at least some traceable paid orders in September"

    for o in target_orders:
        oid = o["order_id"]
        expected = Decimal(o["net_total_cents"]) / 100
        got = credits_by_order.get(oid, Decimal("0"))
        if oid in split_ids:
            # One balance leg may have been given a card alt-method and ended up
            # in a CARD SETTLEMENT batch — traceable amount ≤ net is valid.
            assert got <= expected, (
                f"Split order {oid}: traceable credits {got} exceed net {expected}"
            )
        else:
            assert got == expected, f"Order {oid}: expected credits {expected}, got {got}"


def test_bank_csv_refund_lines_exist(month_files):
    """Refund lines for orders 1012 and 0987 must appear as debits."""
    rows = _read_bank(month_files)
    refund_refs = {row["reference"] for row in rows if Decimal(row["debit"]) > 0}
    assert "ORD1012-REF" in refund_refs, "refund for order 1012 not found"
    assert "ORD0987-REF" in refund_refs, "refund for order 0987 not found"


def test_bank_csv_has_card_settlement_rows(month_files):
    """At least one CARD SETTLEMENT batch row must exist."""
    rows = _read_bank(month_files)
    batches = [r for r in rows if r["description"].startswith("CARD SETTLEMENT")]
    assert batches, "no card settlement rows found"


def test_bank_csv_atome_instalment_payouts(month_files, key):
    """
    Every Atome order that is fully paid in September must have exactly one
    INSTALMENT PAYOUT credit row in bank.csv.

    Per-order arithmetic: payout_credit + _bp_fee(net_total_cents, INSTALMENT_FEE_BP)
    must equal net_total_cents exactly (integer-cent check via Decimal).

    No Atome order may have a DEP or BAL row.
    """
    month = build_month()
    atome_ids = bank_atome_order_ids(month)

    rows = _read_bank(month_files)

    # Index INSTALMENT PAYOUT credits by order_id (from the -INST reference suffix).
    payout_by_order: dict[str, Decimal] = {}
    for row in rows:
        ref = row["reference"]
        if ref.endswith("-INST"):
            oid = ref[3:-5]  # strip leading "ORD" and trailing "-INST"
            payout_by_order[oid] = Decimal(row["credit"])

    # Verify no DEP/BAL rows exist for Atome orders.
    for row in rows:
        ref = row["reference"]
        if ref.startswith("ORD") and not ref.endswith("-REF") and not ref.endswith("-INST"):
            oid = ref[3:]
            assert oid not in atome_ids, (
                f"Atome order {oid} must not have a DEP/BAL row; found reference={ref}"
            )

    # Every Atome order paid in September must have a payout row with correct amount.
    atome_orders_in_sep = [
        o for o in key["golden_orders"]
        if o["payment_method"] == ATOME_METHOD
        and o.get("paid_on") and o["paid_on"].startswith(MONTH)
    ]
    assert atome_orders_in_sep, "expected some Atome orders paid in September"

    for o in atome_orders_in_sep:
        oid = o["order_id"]
        net = o["net_total_cents"]
        fee = _bp_fee(net, INSTALMENT_FEE_BP)
        expected_credit = Decimal(net - fee)
        got = payout_by_order.get(oid)
        assert got is not None, f"Atome order {oid}: no INSTALMENT PAYOUT row found"
        assert got * 100 == expected_credit, (
            f"Atome order {oid}: payout {got} + fee {fee}c != net {net}c"
        )


def test_bank_csv_ambig_all_four_descriptions_appear(month_files):
    """
    All four scripted ambiguous descriptions must appear at least once in bank.csv.
    The ambig pool now excludes Atome orders, so all four are always present.
    """
    rows = _read_bank(month_files)
    descs = {r["description"] for r in rows}
    for expected_desc in ("SOFA LIM", "FURN PAYMENT", "DEPOSIT TAN"):
        # "" (blank) is always in the set since we check non-empty ones here.
        assert expected_desc in descs, f"ambiguous description '{expected_desc}' not in bank.csv"
    # Also check blank description appears.
    assert any(r["description"] == "" for r in rows), "blank description not found in bank.csv"


def test_bank_csv_card_settlement_exact_fee(month_files, key):
    """
    Each CARD SETTLEMENT credit must exactly equal gross − _bp_fee(gross, CARD_SETTLEMENT_FEE_BP),
    where gross is the sum of all card-method transactions on that day.

    bank_card_gross_by_date() replicates the same accumulation logic as build_bank_rows
    (including the split-order alt-method choices) so this comparison is exact.
    """
    month = build_month()
    gross_by_date = bank_card_gross_by_date(month)

    bank_rows = _read_bank(month_files)
    settlement_rows = [r for r in bank_rows if r["description"].startswith("CARD SETTLEMENT")]
    assert settlement_rows, "no CARD SETTLEMENT rows found"

    for row in settlement_rows:
        settle_date = row["date"]
        gross = gross_by_date.get(settle_date, 0)
        assert gross > 0, f"no card gross found for settlement date {settle_date}"
        expected_fee = _bp_fee(gross, CARD_SETTLEMENT_FEE_BP)
        expected_net_cents = gross - expected_fee
        # bank.csv stores dollars with 2 dp; compare in cents to stay integer.
        got_cents = round(Decimal(row["credit"]) * 100)
        assert got_cents == expected_net_cents, (
            f"CARD SETTLEMENT {settle_date}: gross={gross}c, fee={expected_fee}c, "
            f"expected net={expected_net_cents}c, got={got_cents}c"
        )


# ---------------------------------------------------------------------------
# crew_messages.json tests
# ---------------------------------------------------------------------------

def test_crew_messages_schema(month_files):
    msgs = json.loads(month_files["crew_messages.json"].read_text("utf-8"))
    assert msgs, "crew_messages.json must not be empty"
    required = {"message_id", "sent_at", "sender", "text"}
    for m in msgs:
        assert set(m.keys()) == required, f"unexpected keys in {m}"


def test_crew_messages_failed_delivery_present(month_files):
    msgs = json.loads(month_files["crew_messages.json"].read_text("utf-8"))
    failed = [m for m in msgs if FAILED_DELIVERY_ORDER in m["text"] and "fail" in m["text"]]
    assert failed, f"no failed delivery message for order {FAILED_DELIVERY_ORDER}"


def test_crew_messages_sgt_timezone(month_files):
    """sent_at must be ISO 8601 with +08:00 offset (SGT)."""
    msgs = json.loads(month_files["crew_messages.json"].read_text("utf-8"))
    for m in msgs:
        assert "+08:00" in m["sent_at"], f"message {m['message_id']} lacks +08:00 timezone"


def test_crew_messages_sender_is_crew(month_files):
    msgs = json.loads(month_files["crew_messages.json"].read_text("utf-8"))
    crew = {"S10", "S11"}
    for m in msgs:
        assert m["sender"] in crew, f"unexpected sender {m['sender']} in {m['message_id']}"


# ---------------------------------------------------------------------------
# august_payouts.json tests
# ---------------------------------------------------------------------------

def test_august_payouts_schema(month_files):
    rows = json.loads(month_files["august_payouts.json"].read_text("utf-8"))
    assert rows, "august_payouts.json must not be empty"
    required = {"order_id", "staff_id", "amount_cents"}
    for r in rows:
        assert set(r.keys()) == required


def test_august_payouts_matches_answer_key(month_files, key):
    """The flat rows in august_payouts.json must exactly match answer_key['prior_payouts']."""
    rows = json.loads(month_files["august_payouts.json"].read_text("utf-8"))
    prior = key["prior_payouts"]
    # Reconstruct dict from flat list.
    reconstructed: dict[str, dict[str, int]] = {}
    for r in rows:
        reconstructed.setdefault(r["order_id"], {})[r["staff_id"]] = r["amount_cents"]
    assert reconstructed == prior


def test_august_payouts_money_in_cents(month_files):
    rows = json.loads(month_files["august_payouts.json"].read_text("utf-8"))
    for r in rows:
        assert isinstance(r["amount_cents"], int), f"amount_cents must be int, got {type(r['amount_cents'])}"
        assert r["amount_cents"] > 0
