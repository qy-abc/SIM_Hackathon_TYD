"""Commission engine: the six rules as pure functions. No LLM, no I/O.

Rules (plan v1/v2):
  1. Rate: base rate on monthly net sales; tier rate on the part above the threshold.
  2. Earned in the month an order is fully paid and delivered.
  3. Split deals: the order's split (default 100% to whoever served).
  4. Discount penalty: discount above the threshold without manager approval lowers
     that order's rate by the penalty.
  5. Promo bonus: flat bonus per promo-brand mattress, from the plan in force on the order date.
  6. Clawback: a refund on an order already paid out in an earlier month is deducted
     this month.

Interpretation choices (v0.1, to confirm with the team before the 9 Oct freeze):
  - An order is earned on the later of paid_on (balance reached zero) and delivered_on.
  - The plan in force on the ORDER date sets the rates and the promo bonus, so an order
    created 13 Sep and delivered 20 Sep gets the v1 S$50 bonus, not v2's S$80.
  - The tier is cumulative within the month, in (earned_on, order_id) order, per person.
  - Rounding: one round-half-up to the cent per (order, person, kind) line.
  - An order refunded in the same month it was earned was never paid out: it earns nothing
    and needs no clawback.
  - GST: commission base = round_half_up(net_total_cents * BP_DENOMINATOR /
    (BP_DENOMINATOR + plan.gst_rate_bp)).  The split and tier accumulator use this
    GST-exclusive base.
  - Disputed orders: if the order_id is in open_disputes it is held immediately with
    reason "dispute open"; no commission or bonus is earned.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date

from pydantic import BaseModel, ConfigDict

from closer.data.models import Order, Staff
from closer.rules.plan import Plan, plan_for

BP_DENOMINATOR = 10_000


class CommissionLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    staff_id: str
    kind: str  # "commission" | "promo_bonus" | "clawback"
    amount_cents: int
    base_cents: int = 0
    note: str = ""


class Hold(BaseModel):
    """An order that earns nothing this month, and why."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    reason: str


class MonthResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: str  # "YYYY-MM"
    lines: tuple[CommissionLine, ...]
    holds: tuple[Hold, ...]

    def totals(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for line in self.lines:
            out[line.staff_id] += line.amount_cents
        return dict(sorted(out.items()))


def round_bp(numerator: int) -> int:
    """Round (cents x basis points) to whole cents, half up."""
    return (numerator + BP_DENOMINATOR // 2) // BP_DENOMINATOR


def allocate(amount_cents: int, split: Mapping[str, int]) -> dict[str, int]:
    """Split an amount by percent; the last person (by staff_id) absorbs the rounding cent."""
    if sum(split.values()) != 100:
        raise ValueError(f"split must sum to 100, got {dict(split)}")
    staff_ids = sorted(split)
    shares = {sid: (amount_cents * split[sid] * 2 + 100) // 200 for sid in staff_ids[:-1]}
    shares[staff_ids[-1]] = amount_cents - sum(shares.values())
    return shares


def in_month(day: date | None, month: str) -> bool:
    return day is not None and day.strftime("%Y-%m") == month


def earned_before(order: Order, month: str) -> bool:
    return order.earned_on is not None and f"{order.earned_on:%Y-%m}" < month


def hold_reason(order: Order, month: str) -> str | None:
    if order.salesperson is None:
        return "no salesperson recorded"
    if order.delivered_on is None or f"{order.delivered_on:%Y-%m}" > month:
        return "not delivered this month"
    if order.earned_on is None:
        return f"balance due S${max(0, order.net_total_cents - order.paid_cents) / 100:,.2f}"
    if earned_before(order, month):
        return "earned in an earlier month"
    if f"{order.earned_on:%Y-%m}" > month:
        return "paid in full after this month"
    if in_month(order.refunded_on, month):
        return "refunded in the month it was earned"
    return None


def penalty_applies(order: Order, plan: Plan) -> bool:
    return order.discount_pct > plan.discount_threshold_pct and not order.discount_approved_by


def promo_units(order: Order, plan: Plan) -> int:
    return sum(i.qty for i in order.items
               if i.brand == plan.promo_brand and i.category == plan.promo_category)


def compute_month(
    orders: Iterable[Order],
    plans: tuple[Plan, ...],
    staff: Iterable[Staff],
    month: str,
    prior_payouts: Mapping[str, Mapping[str, int]] | None = None,
    open_disputes: set[str] | None = None,
) -> MonthResult:
    """Every commission line for one month.

    prior_payouts: order_id -> staff_id -> cents paid before.
    open_disputes: order_ids whose commission is held pending dispute resolution.
    """
    eligible = {s.staff_id for s in staff if s.commission_eligible}
    disputed = open_disputes or set()
    orders = sorted(orders, key=lambda o: (o.earned_on or date.max, o.order_id))
    lines: list[CommissionLine] = []
    holds: list[Hold] = []
    cumulative: dict[str, int] = defaultdict(int)

    for order in orders:
        if order.order_id in disputed:
            holds.append(Hold(order_id=order.order_id, reason="dispute open"))
            continue
        if in_month(order.refunded_on, month) and earned_before(order, month):
            clawbacks = _clawbacks(order, (prior_payouts or {}).get(order.order_id, {}))
            lines += clawbacks
            if not clawbacks:
                holds.append(Hold(order_id=order.order_id,
                                  reason="refunded, but no earlier payout on record to claw back"))
            continue
        reason = hold_reason(order, month)
        if reason:
            holds.append(Hold(order_id=order.order_id, reason=reason))
            continue
        plan = plan_for(plans, order.created_on)
        gst_base = _gst_exclusive_base(order.net_total_cents, plan)
        net_shares = allocate(gst_base, order.split)
        bonus_shares = allocate(promo_units(order, plan) * plan.promo_bonus_cents, order.split)
        for staff_id in sorted(net_shares):
            if staff_id not in eligible:
                continue
            lines.append(_commission_line(order, plan, staff_id, net_shares[staff_id], cumulative[staff_id]))
            cumulative[staff_id] += net_shares[staff_id]
            if bonus_shares[staff_id]:
                lines.append(CommissionLine(
                    order_id=order.order_id, staff_id=staff_id, kind="promo_bonus",
                    amount_cents=bonus_shares[staff_id],
                    note=f"{promo_units(order, plan)} x {plan.promo_brand} mattress, plan v{plan.version}",
                ))
    return MonthResult(month=month, lines=tuple(lines), holds=tuple(holds))


def _gst_exclusive_base(net_total_cents: int, plan: Plan) -> int:
    """Strip GST from net_total_cents. Round half up.

    base = round_half_up(net_total_cents * 10000 / (10000 + gst_rate_bp))
    If gst_rate_bp == 0 the result is net_total_cents unchanged.
    """
    if plan.gst_rate_bp == 0:
        return net_total_cents
    denom = BP_DENOMINATOR + plan.gst_rate_bp
    return (net_total_cents * BP_DENOMINATOR + denom // 2) // denom


def _commission_line(order: Order, plan: Plan, staff_id: str, share: int, before: int) -> CommissionLine:
    below = max(0, min(share, plan.tier_threshold_cents - before))
    above = share - below
    penalty = plan.discount_penalty_bp if penalty_applies(order, plan) else 0
    amount = round_bp(below * (plan.base_rate_bp - penalty) + above * (plan.tier_rate_bp - penalty))
    notes = [f"plan v{plan.version}"]
    if above:
        notes.append(f"S${above / 100:,.2f} above tier")
    if penalty:
        notes.append(f"{order.discount_pct}% discount without approval: -{penalty / 100:g}pt")
    if len(order.split) > 1:
        notes.append(f"split {order.split[staff_id]}%")
    return CommissionLine(order_id=order.order_id, staff_id=staff_id, kind="commission",
                          amount_cents=amount, base_cents=share, note="; ".join(notes))


def _clawbacks(order: Order, paid: Mapping[str, int]) -> list[CommissionLine]:
    return [
        CommissionLine(order_id=order.order_id, staff_id=sid, kind="clawback", amount_cents=-cents,
                       note=f"refunded {order.refunded_on}, paid out earlier")
        for sid, cents in sorted(paid.items()) if cents
    ]
