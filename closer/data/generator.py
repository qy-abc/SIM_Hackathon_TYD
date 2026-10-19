"""Golden month generator: one seeded September 2026 for a 15-person furniture retailer.

Same seed, same files, byte for byte. Writes what the agents may see (POS orders,
roadshow sheet, bank statement, crew messages, August payouts) and, separately, the
answer key they must never see.

Anomalies seeded here (see the plan's "Seeded anomalies" table):
  5 duplicate orders across POS and roadshow, 3 missing salespeople, 1 discount over
  10% without approval, 2 split deals, 1 failed delivery, 1 return within 14 days,
  1 refund on an order paid out last month, 1 August order paid off in September,
  1 late dispute (order 1043), and the
  mid-month promo change (plan v2 from 15 Sep).
Bank statement anomalies: 8 partial/multi-method payments, 4 ambiguous references,
  daily card settlements batched net of CARD_SETTLEMENT_FEE_BP (180 bp), 3 instalment
  payouts (credit, net of INSTALMENT_FEE_BP 200 bp), refund lines for orders 1012 and 0987.
"""
from __future__ import annotations

import csv
import io
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from closer.data.models import LineItem, Order
from closer.data.reference import reference_totals
from closer.data.staff import load_staff
from closer.rules.commission import compute_month
from closer.rules.plan import load_plans

DEFAULT_SEED = 20260930
MONTH = "2026-09"
MONTH_START, MONTH_END = date(2026, 9, 1), date(2026, 9, 30)
POS_COUNT, ROADSHOW_ONLY_COUNT = 95, 25
SALESPEOPLE = ("S02", "S03", "S04", "S05", "S06")
OWNER = "Jason Lim"
DISPUTED_ORDER, DISPUTE_OWNER, DISPUTE_CLAIMANT = "1043", "S03", "S02"
SPLIT_ORDERS = {"1018": {"S02": 60, "S04": 40}, "1061": {"S05": 60, "S06": 40}}
UNAPPROVED_DISCOUNT_ORDER, UNAPPROVED_DISCOUNT_PCT = "1027", 14
FAILED_DELIVERY_ORDER, RETURNED_ORDER = "1052", "1012"
PRIOR_MONTH_ORDER, LATE_PAID_ORDER = "0987", "0991"
VENUES = ("Expo Hall 6", "Suntec L4", "Tampines Hub")

# Fee constants in integer basis points (1 bp = 0.01%).  Never use floats for money.
CARD_SETTLEMENT_FEE_BP = 180   # 1.80% MDR charged by acquirer, net-settled daily
INSTALMENT_FEE_BP      = 200   # 2.00% taken by instalment provider before payout

CATALOG = (
    ("MT-SLQ", "Slumber Queen Mattress", "mattress", "Slumber", 189900),
    ("MT-SLK", "Slumber King Mattress", "mattress", "Slumber", 239900),
    ("MT-RWQ", "RestWell Queen Mattress", "mattress", "RestWell", 129900),
    ("PL-SL2", "Slumber Pillow Pair", "accessory", "Slumber", 9900),
    ("SF-OS3", "Oslo 3-Seater Sofa", "sofa", "Nordhaus", 219900),
    ("SF-BGL", "Bergen L-Shape Sofa", "sofa", "Nordhaus", 349900),
    ("RC-ARM", "Recliner Armchair", "sofa", "Nordhaus", 99900),
    ("BF-KYQ", "Kyoto Queen Bedframe", "bedframe", "Hinoki", 89900),
    ("DT-TK6", "Teak 6-Seater Dining Table", "dining", "Hinoki", 159900),
    ("CH-DIN", "Dining Chair", "dining", "Hinoki", 19900),
    ("WR-3DR", "3-Door Wardrobe", "wardrobe", "Nordhaus", 119900),
    ("TV-CON", "TV Console", "living", "Hinoki", 69900),
)
SURNAMES = ("Tan", "Lim", "Lee", "Ng", "Wong", "Goh", "Chua", "Ong", "Koh", "Teo", "Sim", "Yeo")
GIVEN = ("Ah Kow", "Mei Hua", "Boon Huat", "Siew Ling", "Wei Ming", "Kok Leong", "Hui Min", "Jun Jie")
OTHER_NAMES = ("Nur Aisyah", "Muhammad Faizal", "Rajesh Kumar", "Priyanka Menon", "Daniel Fernandez")
METHODS = ("PayNow", "NETS", "Visa", "Mastercard", "Atome", "Cash")

# Visa/Mastercard are batched into daily net card-settlement lines.
# Atome is a BNPL instalment provider: no deposit/balance rows; instead a single
# "INSTALMENT PAYOUT" credit lands 2 days after paid_on, net of INSTALMENT_FEE_BP.
CARD_METHODS = {"Visa", "Mastercard"}
ATOME_METHOD = "Atome"

_SGT = ZoneInfo("Asia/Singapore")


def _customer(rng: random.Random, used_phones: set[str]) -> tuple[str, str]:
    name = (f"{rng.choice(SURNAMES)} {rng.choice(GIVEN)}" if rng.random() < 0.8
            else rng.choice(OTHER_NAMES))
    while (phone := f"9{rng.randrange(1_000_000, 9_999_999)}") in used_phones:
        pass
    used_phones.add(phone)
    return name, phone


def name_variant(name: str) -> str:
    """How a roadshow helper types a POS name: 'Tan Ah Kow' -> 'Mr Tan A.K.'."""
    surname, *given = name.split()
    initials = ".".join(g[0] for g in given) + "."
    return f"Mr {surname} {initials}" if given else name


def _items(rng: random.Random) -> tuple[LineItem, ...]:
    picks = rng.sample(CATALOG, k=rng.choice((1, 1, 2, 2, 3)))
    return tuple(LineItem(sku=s, name=n, category=c, brand=b, qty=4 if s == "CH-DIN" else 1, unit_cents=p)
                 for s, n, c, b, p in picks)


def _discount(rng: random.Random, order_id: str) -> tuple[int, str | None]:
    if order_id == UNAPPROVED_DISCOUNT_ORDER:
        return UNAPPROVED_DISCOUNT_PCT, None
    pct = rng.choice((0, 0, 0, 5, 5, 8, 10, 10, 12, 15))
    return pct, (OWNER if pct > 10 else None)


def _make_order(rng: random.Random, order_id: str, source: str, day: date, phones: set[str],
                salesperson: str) -> Order:
    name, phone = _customer(rng, phones)
    items = _items(rng)
    list_total = sum(i.qty * i.unit_cents for i in items)
    pct, approver = _discount(rng, order_id)
    net = list_total * (100 - pct) // 100
    delivered = day + timedelta(days=rng.randint(2, 10))
    paid_on = day + timedelta(days=rng.randint(0, 7))
    fully_paid = rng.random() < 0.9 and paid_on <= MONTH_END
    return Order(
        order_id=order_id, source=source, created_on=day, customer_name=name, phone=phone, items=items,
        list_total_cents=list_total, discount_pct=pct, discount_approved_by=approver, net_total_cents=net,
        salesperson=salesperson, split=SPLIT_ORDERS.get(order_id, {salesperson: 100}),
        payment_method=rng.choice(METHODS), paid_cents=net if fully_paid else net * 30 // 100,
        paid_on=paid_on if fully_paid else None,
        delivered_on=delivered if delivered <= MONTH_END else None,
    )


def _apply_story(order: Order) -> Order:
    """Pin the scripted anomalies onto specific orders."""
    if order.order_id == DISPUTED_ORDER:
        return order.model_copy(update={"salesperson": DISPUTE_OWNER, "split": {DISPUTE_OWNER: 100}})
    if order.order_id in SPLIT_ORDERS:
        first = sorted(SPLIT_ORDERS[order.order_id], key=lambda s: -SPLIT_ORDERS[order.order_id][s])[0]
        return order.model_copy(update={"salesperson": first})
    if order.order_id == FAILED_DELIVERY_ORDER:
        return order.model_copy(update={"delivered_on": None, "paid_cents": order.net_total_cents,
                                        "paid_on": order.created_on})
    if order.order_id == RETURNED_ORDER:
        delivered = date(2026, 9, 8)
        return order.model_copy(update={"created_on": date(2026, 9, 3), "delivered_on": delivered,
                                        "paid_cents": order.net_total_cents, "paid_on": date(2026, 9, 3),
                                        "refunded_on": delivered + timedelta(days=10)})
    return order


def _august_order(rng: random.Random, phones: set[str], order_id: str, salesperson: str,
                  delivered: date, paid: date, refunded: date | None = None) -> Order:
    base = _make_order(rng, order_id, "pos", date(2026, 8, 20), phones, salesperson)
    return base.model_copy(update={"delivered_on": delivered, "paid_cents": base.net_total_cents,
                                   "paid_on": paid, "refunded_on": refunded})


def _prior_month_orders(rng: random.Random, phones: set[str]) -> tuple[list[Order], dict[str, dict[str, int]]]:
    """0987: paid out in August, refunded in September. 0991: delivered in August, paid off in September."""
    refunded = _august_order(rng, phones, PRIOR_MONTH_ORDER, "S04",
                             date(2026, 8, 25), date(2026, 8, 20), refunded=date(2026, 9, 10))
    late_paid = _august_order(rng, phones, LATE_PAID_ORDER, "S05", date(2026, 8, 28), date(2026, 9, 4))
    # What August's close paid on 0987, from the engine itself (alone in August, so under the tier).
    august = compute_month([refunded.model_copy(update={"refunded_on": None})],
                           load_plans(), load_staff(), "2026-08")
    return [refunded, late_paid], {PRIOR_MONTH_ORDER: august.totals()}


def build_month(seed: int = DEFAULT_SEED) -> dict:
    rng = random.Random(seed)
    phones: set[str] = set()
    days = sorted(MONTH_START + timedelta(days=rng.randint(0, 27)) for _ in range(POS_COUNT))
    pos = [_apply_story(_make_order(rng, str(1001 + i), "pos", d, phones, rng.choice(SALESPEOPLE)))
           for i, d in enumerate(days)]
    roadshow_days = sorted(MONTH_START + timedelta(days=rng.randint(0, 27)) for _ in range(ROADSHOW_ONLY_COUNT))
    roadshow = [_make_order(rng, f"RS-{i + 1:02d}", "roadshow", d, phones, rng.choice(SALESPEOPLE))
                for i, d in enumerate(roadshow_days)]
    prior, prior_payouts = _prior_month_orders(rng, phones)
    duplicates = sorted(rng.sample([o for o in pos if o.order_id not in _scripted_ids()
                                    and len(o.customer_name.split()) == 3], k=5), key=lambda o: o.order_id)
    missing = rng.sample(roadshow, k=3)
    return {"seed": seed, "pos": pos, "roadshow_only": roadshow, "prior": prior,
            "prior_payouts": prior_payouts, "duplicates": duplicates, "missing": missing}


def _scripted_ids() -> set[str]:
    return {DISPUTED_ORDER, UNAPPROVED_DISCOUNT_ORDER, FAILED_DELIVERY_ORDER, RETURNED_ORDER, *SPLIT_ORDERS}


def roadshow_rows(month: dict, rng: random.Random) -> list[dict]:
    """The sheet as typed at the roadshow: roadshow-only sales plus the 5 that were also keyed into POS."""
    missing_ids = {o.order_id for o in month["missing"]}
    entries = [(o, False) for o in month["roadshow_only"]] + [(o, True) for o in month["duplicates"]]
    entries.sort(key=lambda e: (e[0].created_on, e[0].order_id))
    rows = []
    for n, (o, dup) in enumerate(entries, start=1):
        rows.append({
            "row": n, "date": o.created_on.isoformat(), "venue": rng.choice(VENUES),
            "customer_name": name_variant(o.customer_name) if dup else o.customer_name, "phone": o.phone,
            "items_text": ", ".join(f"{i.qty}x {i.name}" for i in o.items),
            "amount": f"{o.net_total_cents / 100:.2f}", "deposit": f"{o.net_total_cents * 30 // 100 / 100:.2f}",
            "salesperson": "" if o.order_id in missing_ids else o.salesperson,
            "keyed_by": "S07", "notes": "also keyed in till" if dup and n % 2 else "",
            "_order_id": o.order_id,
        })
    return rows


def pos_record(o: Order) -> dict:
    """Only what a till knows: no delivery or refund truth."""
    return {
        "order_id": o.order_id, "created_at": o.created_on.isoformat(), "customer_name": o.customer_name,
        "phone": o.phone, "items": [i.model_dump() for i in o.items], "list_total_cents": o.list_total_cents,
        "discount_pct": o.discount_pct, "discount_approved_by": o.discount_approved_by,
        "net_total_cents": o.net_total_cents, "salesperson": o.salesperson, "split": o.split,
        "deposit_cents": o.net_total_cents * 30 // 100, "payment_method": o.payment_method,
    }


# ---------------------------------------------------------------------------
# Bank statement generator
# ---------------------------------------------------------------------------

_BP = 10_000  # basis-point denominator, matches closer/rules/commission.py


def _bp_fee(amount_cents: int, fee_bp: int) -> int:
    """Compute a fee in integer cents: amount × fee_bp / 10 000, half-up."""
    return (amount_cents * fee_bp + _BP // 2) // _BP


def _fmt_cents(cents: int) -> str:
    """Format integer cents as 'd.cc' with no float arithmetic."""
    return f"{cents // 100}.{cents % 100:02d}"


def _bank_ref(order_id: str, suffix: str = "") -> str:
    return f"ORD{order_id}{suffix}"


# ---------------------------------------------------------------------------
# Shared context builder — single source of truth for all bank picks.
# Called by build_bank_rows (live) and the exported test helpers (replay).
# ---------------------------------------------------------------------------

def _build_bank_context(month: dict, rng: random.Random) -> dict:
    """
    Compute all derived sets/maps needed to build the bank statement:
      split_order_ids   – 8 POS orders whose balance is split into two method legs
      split_alt         – {order_id: alt_method} for the second leg of each split
      ambig_ids         – 4 POS orders given ambiguous descriptions / blank references
      ambig_map         – {order_id: (description, reference)}
      atome_order_ids   – every order paid via Atome (gets an INSTALMENT PAYOUT row)
      events            – all deposit/balance events after split expansion
      card_by_date      – {date: gross_cents} for Visa/MC card events (not Atome)

    The rng must be initialised to seed+2 before calling.
    """
    all_orders: list[Order] = [*month["pos"], *month["roadshow_only"], *month["prior"]]

    # ------------------------------------------------------------------ #
    # Atome orders – these get NO deposit/balance rows; handled separately
    # ------------------------------------------------------------------ #
    atome_order_ids: set[str] = {
        o.order_id for o in all_orders
        if o.payment_method == ATOME_METHOD
    }

    # ------------------------------------------------------------------ #
    # Raw deposit + balance events (skip Atome orders entirely)           #
    # ------------------------------------------------------------------ #
    raw_events: list[tuple[date, str, str, str, int]] = []
    for o in all_orders:
        if o.order_id in atome_order_ids:
            continue  # Atome → instalment payout, not deposit/balance
        dep = o.net_total_cents * 30 // 100
        if o.created_on.strftime("%Y-%m") == MONTH:
            raw_events.append((o.created_on, o.order_id, "deposit", o.payment_method, dep))
        if o.paid_on is not None and o.paid_on.strftime("%Y-%m") == MONTH:
            bal = o.net_total_cents - dep
            if bal > 0:
                raw_events.append((o.paid_on, o.order_id, "balance", o.payment_method, bal))

    # ------------------------------------------------------------------ #
    # Split-payment pick: 8 non-scripted, non-Atome POS orders            #
    # ------------------------------------------------------------------ #
    eligible_for_split = [
        o for o in month["pos"]
        if o.order_id not in _scripted_ids()
        and o.order_id not in atome_order_ids
        and o.paid_on is not None
        and o.paid_on.strftime("%Y-%m") == MONTH
        and (o.net_total_cents - o.net_total_cents * 30 // 100) > 0
    ]
    split_payment_orders = rng.sample(eligible_for_split, k=min(8, len(eligible_for_split)))
    split_order_ids: set[str] = {o.order_id for o in split_payment_orders}

    # Expand split-order balance events into two legs, recording alt method.
    split_alt: dict[str, str] = {}
    events: list[tuple[date, str, str, str, int]] = []
    for ev in sorted(raw_events, key=lambda e: (e[0], e[1], e[2])):
        ev_date, oid, kind, method, amount = ev
        if oid in split_order_ids and kind == "balance":
            first = amount // 2
            second = amount - first
            alt = rng.choice([m for m in METHODS if m != method])
            split_alt[oid] = alt
            events.append((ev_date, oid, "balance_part1", method, first))
            events.append((ev_date, oid, "balance_part2", alt, second))
        else:
            events.append(ev)

    # ------------------------------------------------------------------ #
    # Ambiguous-reference pick: 4 non-scripted, non-split, non-Atome POS  #
    # orders that have at least one NON-CARD event (so the description     #
    # actually appears as a row rather than disappearing into CARD batch). #
    # ------------------------------------------------------------------ #
    non_card_visible_oids = {
        oid for (_, oid, _, method, _) in events
        if method not in CARD_METHODS
    }
    ambig_pool = [
        o for o in month["pos"]
        if o.order_id not in _scripted_ids()
        and o.order_id not in split_order_ids
        and o.order_id not in atome_order_ids
        and o.order_id in non_card_visible_oids
    ]
    ambig_orders = rng.sample(ambig_pool, k=min(4, len(ambig_pool)))
    ambig_ids: set[str] = {o.order_id for o in ambig_orders}

    _AMBIG_DESCS = [
        ("SOFA LIM", ""),
        ("FURN PAYMENT", "PAY"),
        ("", ""),
        ("DEPOSIT TAN", ""),
    ]
    ambig_map: dict[str, tuple[str, str]] = {
        o.order_id: pair for o, pair in zip(ambig_orders, _AMBIG_DESCS)
    }

    # ------------------------------------------------------------------ #
    # Card gross by date (Visa/Mastercard only, not Atome)                #
    # ------------------------------------------------------------------ #
    card_by_date: dict[date, int] = {}
    for ev_date, oid, kind, method, amount in events:
        if method in CARD_METHODS:
            card_by_date[ev_date] = card_by_date.get(ev_date, 0) + amount

    return {
        "all_orders": all_orders,
        "atome_order_ids": atome_order_ids,
        "split_order_ids": split_order_ids,
        "split_alt": split_alt,
        "ambig_ids": ambig_ids,
        "ambig_map": ambig_map,
        "events": events,
        "card_by_date": card_by_date,
    }


# ---------------------------------------------------------------------------
# Public helpers – delegate entirely to _build_bank_context
# ---------------------------------------------------------------------------

def bank_split_order_ids(month: dict, seed: int = DEFAULT_SEED) -> frozenset[str]:
    """Return the 8 order_ids whose balance was split across two payment methods."""
    ctx = _build_bank_context(month, random.Random(seed + 2))
    return frozenset(ctx["split_order_ids"])


def bank_ambig_order_ids(month: dict, seed: int = DEFAULT_SEED) -> frozenset[str]:
    """Return the 4 order_ids assigned ambiguous bank references."""
    ctx = _build_bank_context(month, random.Random(seed + 2))
    return frozenset(ctx["ambig_ids"])


def bank_card_gross_by_date(month: dict, seed: int = DEFAULT_SEED) -> dict[str, int]:
    """Return {ISO date → total gross Visa/MC cents} for each card-settlement day."""
    ctx = _build_bank_context(month, random.Random(seed + 2))
    return {d.isoformat(): g for d, g in ctx["card_by_date"].items()}


def bank_atome_order_ids(month: dict) -> frozenset[str]:
    """Return all order_ids paid via Atome (each gets one INSTALMENT PAYOUT row)."""
    all_orders: list[Order] = [*month["pos"], *month["roadshow_only"], *month["prior"]]
    return frozenset(o.order_id for o in all_orders if o.payment_method == ATOME_METHOD)


def build_bank_rows(month: dict, rng: random.Random) -> list[dict]:
    """
    Build the DBS-style bank statement rows for September 2026.

    Columns: date, value_date, description, reference, debit, credit, balance.
    All money is integer cents internally; the running balance is computed last.

    Atome orders: no deposit/balance rows; one INSTALMENT PAYOUT credit on paid_on+2.
    Visa/Mastercard: batched into a daily CARD SETTLEMENT credit, net of CARD_SETTLEMENT_FEE_BP.
    """
    ctx = _build_bank_context(month, rng)
    all_orders   = ctx["all_orders"]
    atome_ids    = ctx["atome_order_ids"]
    split_ids    = ctx["split_order_ids"]
    ambig_map    = ctx["ambig_map"]
    events       = ctx["events"]
    card_by_date = ctx["card_by_date"]

    # ------------------------------------------------------------------ #
    # Refund lines for RETURNED_ORDER and PRIOR_MONTH_ORDER               #
    # ------------------------------------------------------------------ #
    refund_events: list[tuple[date, str, str, str, int]] = []
    for o in all_orders:
        if o.order_id in (RETURNED_ORDER, PRIOR_MONTH_ORDER) and o.refunded_on is not None:
            if o.refunded_on.strftime("%Y-%m") == MONTH:
                refund_events.append(
                    (o.refunded_on, o.order_id, "refund", o.payment_method, o.net_total_cents)
                )

    # ------------------------------------------------------------------ #
    # Atome instalment payout rows (one per Atome order paid in MONTH)    #
    # ------------------------------------------------------------------ #
    atome_rows: list[dict] = []
    for o in all_orders:
        if o.order_id not in atome_ids:
            continue
        if o.paid_on is None or o.paid_on.strftime("%Y-%m") != MONTH:
            continue
        fee = _bp_fee(o.net_total_cents, INSTALMENT_FEE_BP)
        net = o.net_total_cents - fee
        payout_date = min(o.paid_on + timedelta(days=2), MONTH_END)
        atome_rows.append({
            "date": payout_date,
            "description": f"INSTALMENT PAYOUT ORD{o.order_id}",
            "reference": _bank_ref(o.order_id, "-INST"),
            "debit": 0, "credit": net,
            "method": ATOME_METHOD, "order_id": o.order_id, "kind": "instalment_payout",
        })

    # ------------------------------------------------------------------ #
    # Non-card, non-Atome deposit/balance rows                            #
    # ------------------------------------------------------------------ #
    def _make_raw(ev_date: date, oid: str, kind: str, method: str, amount: int) -> dict:
        if oid in ambig_map and kind in ("deposit", "balance", "balance_part1", "balance_part2"):
            desc, ref = ambig_map[oid]
        else:
            if kind == "deposit":
                desc = f"ORD{oid} DEP"
            elif kind == "balance":
                desc = f"ORD{oid} BAL"
            elif kind == "balance_part1":
                desc = f"ORD{oid} BAL PT1"
            else:
                desc = f"ORD{oid} BAL PT2"
            ref = _bank_ref(oid)
        return {"date": ev_date, "description": desc, "reference": ref,
                "debit": 0, "credit": amount, "method": method, "order_id": oid, "kind": kind}

    non_card_rows: list[dict] = []
    all_raw_events = sorted(events + refund_events, key=lambda e: (e[0], e[1], e[2]))
    for ev_date, oid, kind, method, amount in all_raw_events:
        if kind == "refund":
            non_card_rows.append({
                "date": ev_date, "description": f"REFUND ORD{oid}",
                "reference": _bank_ref(oid, "-REF"),
                "debit": amount, "credit": 0,
                "method": method, "order_id": oid, "kind": kind,
            })
        elif method in CARD_METHODS:
            pass  # already captured in card_by_date inside _build_bank_context
        else:
            non_card_rows.append(_make_raw(ev_date, oid, kind, method, amount))

    # ------------------------------------------------------------------ #
    # Batched Visa/Mastercard settlement rows                             #
    # ------------------------------------------------------------------ #
    card_settlement_rows: list[dict] = []
    for settle_date in sorted(card_by_date):
        gross = card_by_date[settle_date]
        fee = _bp_fee(gross, CARD_SETTLEMENT_FEE_BP)
        net = gross - fee
        value_date = min(settle_date + timedelta(days=1), MONTH_END)
        card_settlement_rows.append({
            "date": settle_date,
            "description": f"CARD SETTLEMENT {settle_date.isoformat()}",
            "reference": f"CSETL{settle_date.strftime('%Y%m%d')}",
            "debit": 0, "credit": net,
            "method": "card_batch", "order_id": "",
            "kind": "card_settlement", "_value_date_override": value_date,
        })

    # ------------------------------------------------------------------ #
    # Merge, sort, compute running balance                                #
    # ------------------------------------------------------------------ #
    all_rows = sorted(
        non_card_rows + card_settlement_rows + atome_rows,
        key=lambda r: (r["date"], r["kind"], r.get("order_id", ""), r["description"]),
    )

    balance = 4_285_320  # cents — S$42,853.20 opening balance
    output: list[dict] = []
    for r in all_rows:
        balance += r["credit"] - r["debit"]
        value_date = r.pop("_value_date_override", None) or (r["date"] + timedelta(days=1))
        r.pop("method", None)
        r.pop("order_id", None)
        r.pop("kind", None)
        output.append({
            "date": r["date"].isoformat(),
            "value_date": value_date.isoformat(),
            "description": r["description"],
            "reference": r["reference"],
            "debit": _fmt_cents(r["debit"]),
            "credit": _fmt_cents(r["credit"]),
            "balance": _fmt_cents(balance),
        })
    return output


# ---------------------------------------------------------------------------
# Crew messages generator
# ---------------------------------------------------------------------------

def build_crew_messages(month: dict, rng: random.Random) -> list[dict]:
    """
    One message per delivery (including failed) in MONTH.
    Fields: message_id, sent_at (Asia/Singapore), sender, text.
    """
    crew = ["S09", "S10", "S11"]
    all_orders: list[Order] = [*month["pos"], *month["roadshow_only"], *month["prior"]]

    messages = []
    msg_id = 1

    # Collect delivered or failed-delivery orders in September.
    delivery_orders: list[Order] = []
    for o in all_orders:
        if o.order_id == FAILED_DELIVERY_ORDER:
            delivery_orders.append(o)
        elif o.delivered_on is not None and o.delivered_on.strftime("%Y-%m") == MONTH:
            delivery_orders.append(o)

    # Sort by delivered_on (use created_on + 3 days as proxy for failed delivery).
    def _sort_key(o: Order) -> date:
        return o.delivered_on if o.delivered_on else o.created_on + timedelta(days=3)

    delivery_orders.sort(key=_sort_key)

    for o in delivery_orders:
        sender = rng.choice(crew)
        # Generate sent_at: delivery date (or estimated) at a random time during working hours.
        base_date = o.delivered_on if o.delivered_on else (o.created_on + timedelta(days=3))
        hour = rng.randint(9, 18)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)
        local_dt = datetime(base_date.year, base_date.month, base_date.day, hour, minute, second,
                            tzinfo=_SGT)
        sent_at = local_dt.isoformat()

        # Scripted: FAILED_DELIVERY_ORDER must be a failed delivery message.
        if o.order_id == FAILED_DELIVERY_ORDER:
            text = f"#{o.order_id} fail cust not home try sat"
        elif o.order_id == RETURNED_ORDER:
            text = f"#{o.order_id} done but cust want return"
        else:
            # Mix of short informal styles.
            style = rng.randint(0, 3)
            if style == 0:
                text = f"#{o.order_id} done"
            elif style == 1:
                text = f"{o.order_id} delivered ok"
            elif style == 2:
                text = f"order {o.order_id} complete"
            else:
                text = f"#{o.order_id} ok cust happy"

        messages.append({
            "message_id": f"MSG{msg_id:04d}",
            "sent_at": sent_at,
            "sender": sender,
            "text": text,
        })
        msg_id += 1

    return messages


# ---------------------------------------------------------------------------
# August payouts generator
# ---------------------------------------------------------------------------

def build_august_payouts(month: dict) -> list[dict]:
    """
    Flat list of {order_id, staff_id, amount_cents} for every commission already
    paid out in August (only PRIOR_MONTH_ORDER = 0987 qualifies).
    """
    rows = []
    for order_id, staff_map in sorted(month["prior_payouts"].items()):
        for staff_id, amount_cents in sorted(staff_map.items()):
            rows.append({
                "order_id": order_id,
                "staff_id": staff_id,
                "amount_cents": amount_cents,
            })
    return rows


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------

def answer_key(month: dict, rows: list[dict]) -> dict:
    missing_ids = {o.order_id for o in month["missing"]}
    open_disputes = {DISPUTED_ORDER}

    # Golden orders: missing-salesperson roadshow entries have salesperson=None
    # (they were not attributed at the time of close; the true owner is in
    # missing_salesperson for Intake scoring).  Disputed orders keep their
    # recorded salesperson so agents can see the ownership; they are excluded
    # from commission by the open_disputes set.
    golden_raw = [*month["pos"], *month["roadshow_only"], *month["prior"]]
    golden = [
        o.model_copy(update={"salesperson": None, "split": {}}) if o.order_id in missing_ids else o
        for o in golden_raw
    ]

    eligible = {s.staff_id for s in load_staff() if s.commission_eligible}
    totals = reference_totals(golden, load_plans(), eligible, MONTH, month["prior_payouts"],
                              open_disputes=open_disputes)
    row_of = {r["_order_id"]: r["row"] for r in rows}
    # True salesperson is taken from the original roadshow order (before nulling).
    missing_sp_source = {o.order_id: o for o in month["roadshow_only"]}
    return {
        "seed": month["seed"], "month": MONTH,
        "golden_orders": [o.model_dump(mode="json") for o in golden],
        "duplicates": [{"pos_order_id": o.order_id, "roadshow_row": row_of[o.order_id]} for o in month["duplicates"]],
        "missing_salesperson": [{"roadshow_row": row_of[o.order_id], "order_id": o.order_id,
                                 "true_salesperson": missing_sp_source[o.order_id].salesperson}
                                for o in month["missing"]],
        "prior_payouts": month["prior_payouts"],
        "open_disputes": sorted(open_disputes),
        "commission_totals_cents": totals,
        "expected_exceptions": [
            {"type": "unapproved_discount", "order_id": UNAPPROVED_DISCOUNT_ORDER, "caught_by": "Commission"},
            {"type": "split_deal", "order_id": "1018", "caught_by": "Commission"},
            {"type": "split_deal", "order_id": "1061", "caught_by": "Commission"},
            {"type": "failed_delivery", "order_id": FAILED_DELIVERY_ORDER, "caught_by": "Fulfilment"},
            {"type": "return_within_window", "order_id": RETURNED_ORDER, "caught_by": "Commission"},
            {"type": "prior_month_refund", "order_id": PRIOR_MONTH_ORDER, "caught_by": "Commission",
             "approval": "owner"},
            {"type": "paid_after_delivery_month", "order_id": LATE_PAID_ORDER, "caught_by": "Commission",
             "detail": "delivered 2026-08-28, balance cleared 2026-09-04: earns in September"},
            {"type": "late_dispute", "order_id": DISPUTED_ORDER, "caught_by": "Supervisor",
             "detail": f"{DISPUTE_CLAIMANT} claims 50/50; recorded owner {DISPUTE_OWNER} 100%"},
            {"type": "plan_change", "detail": "promo bonus S$50 -> S$80 from 2026-09-15", "caught_by": "Commission"},
        ],
    }


# ---------------------------------------------------------------------------
# write_month — entry point
# ---------------------------------------------------------------------------

def write_month(out_dir: Path, seed: int = DEFAULT_SEED) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    month = build_month(seed)
    rows = roadshow_rows(month, random.Random(seed + 1))
    bank_rng = random.Random(seed + 2)
    crew_rng = random.Random(seed + 3)

    paths = {name: out_dir / name for name in (
        "pos_orders.json", "roadshow.csv", "answer_key.json",
        "bank.csv", "crew_messages.json", "august_payouts.json",
    )}

    # pos_orders.json
    paths["pos_orders.json"].write_text(
        json.dumps([pos_record(o) for o in month["pos"]], indent=2), "utf-8"
    )

    # roadshow.csv
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[k for k in rows[0] if not k.startswith("_")],
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    paths["roadshow.csv"].write_text(buf.getvalue(), "utf-8")

    # bank.csv
    bank_rows = build_bank_rows(month, bank_rng)
    bank_buf = io.StringIO()
    bank_writer = csv.DictWriter(
        bank_buf,
        fieldnames=["date", "value_date", "description", "reference", "debit", "credit", "balance"],
        lineterminator="\n",
    )
    bank_writer.writeheader()
    bank_writer.writerows(bank_rows)
    paths["bank.csv"].write_text(bank_buf.getvalue(), "utf-8")

    # crew_messages.json
    crew_msgs = build_crew_messages(month, crew_rng)
    paths["crew_messages.json"].write_text(json.dumps(crew_msgs, indent=2), "utf-8")

    # august_payouts.json
    aug_payouts = build_august_payouts(month)
    paths["august_payouts.json"].write_text(json.dumps(aug_payouts, indent=2), "utf-8")

    # answer_key.json (written last, never read by agents)
    paths["answer_key.json"].write_text(json.dumps(answer_key(month, rows), indent=2), "utf-8")

    return paths
