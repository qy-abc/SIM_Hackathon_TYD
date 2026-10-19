"""The loader rebuilds the month from the agent-visible files alone and lands on the answer key."""
import json

import pytest

from closer.data.staff import load_staff
from closer.data.generator import write_month
from closer.rules.commission import compute_month
from closer.rules.plan import load_plans
from closer.sources.loader import load_period, suggest_matches

MONTH = "2026-09"


@pytest.fixture(scope="module")
def month_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("golden")
    paths = write_month(out)
    key = json.loads(paths["answer_key.json"].read_text("utf-8"))
    paths["answer_key.json"].unlink()  # the loader must work without it
    return out, key


def approve_unique(data_dir):
    """Accept every suggestion with exactly one candidate, as a person would on review."""
    approved: dict[int, str] = {}
    while True:
        new = {s.line_no: s.candidates[0] for s in suggest_matches(load_period(MONTH, data_dir, approved))
               if len(s.candidates) == 1 and s.line_no not in approved}
        if not new:
            return approved
        approved.update(new)


def totals(period):
    result = compute_month(period.orders, load_plans(), load_staff(), MONTH, period.prior_payouts,
                           open_disputes={"1043"})
    return {k: v for k, v in result.totals().items() if v}


def test_totals_match_answer_key_after_review(month_dir):
    data_dir, key = month_dir
    period = load_period(MONTH, data_dir, approve_unique(data_dir))
    assert totals(period) == key["commission_totals_cents"]


def test_unreferenced_credits_wait_for_a_person(month_dir):
    data_dir, _ = month_dir
    period = load_period(MONTH, data_dir)
    suggestions = suggest_matches(period)
    assert {s.description for s in suggestions} == {"SOFA LIM", "DEPOSIT TAN", "FURN PAYMENT", "(blank)"}
    assert all(len(s.candidates) == 1 for s in suggestions)
    unpaid = {c for s in suggestions for c in s.candidates}
    assert all(o.paid_on is None for o in period.orders if o.order_id in unpaid)


def test_roadshow_duplicates_skipped(month_dir):
    data_dir, key = month_dir
    period = load_period(MONTH, data_dir)
    assert {int(r["row"]) for r in period.skipped_duplicates} == {d["roadshow_row"] for d in key["duplicates"]}


def test_failed_delivery_and_last_month_orders(month_dir):
    data_dir, _ = month_dir
    orders = {o.order_id: o for o in load_period(MONTH, data_dir).orders}
    assert orders["1052"].delivered_on is None
    assert str(orders["0991"].earned_on) == "2026-09-04"
    assert str(orders["0987"].refunded_on) == "2026-09-10"


def test_card_settlements_add_up_to_bank_credits(month_dir):
    data_dir, _ = month_dir
    import csv
    from collections import defaultdict
    from closer.sources.loader import cents
    gross = defaultdict(int)
    with (data_dir / "settlements.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            gross[row["batch_ref"]] += cents(row["gross"])
    with (data_dir / "bank.csv").open(encoding="utf-8", newline="") as f:
        credit = {r["reference"]: cents(r["credit"]) for r in csv.DictReader(f)}
    for ref, amount in gross.items():
        fee_bp = 200 if ref.endswith("-INST") else 180
        assert amount - (amount * fee_bp + 5000) // 10000 == credit[ref], ref
