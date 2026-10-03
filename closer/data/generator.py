"""Golden month generator: one seeded September 2026 for a 15-person furniture retailer.

Same seed, same files, byte for byte. Writes what the agents may see (POS orders,
roadshow sheet) and, separately, the answer key they must never see.

Anomalies seeded here (see the plan's "Seeded anomalies" table):
  5 duplicate orders across POS and roadshow, 3 missing salespeople, 1 discount over
  10% without approval, 2 split deals, 1 failed delivery, 1 return within 14 days,
  1 refund on an order paid out last month, 1 August order paid off in September,
  1 late dispute (order 1043), and the
  mid-month promo change (plan v2 from 15 Sep).
Not generated yet: the bank statement and crew messages (partial payments, ambiguous
references, batched card settlements, instalment payouts).
"""
from __future__ import annotations

import csv
import io
import json
import random
from datetime import date, timedelta
from pathlib import Path

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


def answer_key(month: dict, rows: list[dict]) -> dict:
    golden = [*month["pos"], *month["roadshow_only"], *month["prior"]]
    eligible = {s.staff_id for s in load_staff() if s.commission_eligible}
    totals = reference_totals(golden, load_plans(), eligible, MONTH, month["prior_payouts"])
    row_of = {r["_order_id"]: r["row"] for r in rows}
    return {
        "seed": month["seed"], "month": MONTH,
        "golden_orders": [o.model_dump(mode="json") for o in golden],
        "duplicates": [{"pos_order_id": o.order_id, "roadshow_row": row_of[o.order_id]} for o in month["duplicates"]],
        "missing_salesperson": [{"roadshow_row": row_of[o.order_id], "order_id": o.order_id,
                                 "true_salesperson": o.salesperson} for o in month["missing"]],
        "prior_payouts": month["prior_payouts"],
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
        "not_yet_generated": ["bank_statement", "crew_messages"],
    }


def write_month(out_dir: Path, seed: int = DEFAULT_SEED) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    month = build_month(seed)
    rows = roadshow_rows(month, random.Random(seed + 1))
    paths = {name: out_dir / name for name in ("pos_orders.json", "roadshow.csv", "answer_key.json")}
    paths["pos_orders.json"].write_text(json.dumps([pos_record(o) for o in month["pos"]], indent=2), "utf-8")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[k for k in rows[0] if not k.startswith("_")],
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    paths["roadshow.csv"].write_text(buf.getvalue(), "utf-8")
    paths["answer_key.json"].write_text(json.dumps(answer_key(month, rows), indent=2), "utf-8")
    return paths
