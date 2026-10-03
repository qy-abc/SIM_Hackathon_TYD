"""Reference commission calculation used only to write the answer key.

Deliberately written apart from closer.rules.commission (person-first rather than
order-first) so the engine is checked against a second reading of the same rules,
not against itself. If the two ever disagree, read the rule text, not either program.
"""
from __future__ import annotations

from datetime import date

from closer.data.models import Order
from closer.rules.plan import Plan


def _plan_on(plans: tuple[Plan, ...], day: date) -> Plan:
    return max((p for p in plans if p.effective_from <= day), key=lambda p: p.effective_from)


def _share(amount: int, split: dict[str, int], who: str) -> int:
    people = sorted(split)
    if who == people[-1]:
        return amount - sum(_share(amount, split, p) for p in people[:-1])
    return (amount * split[who] * 2 + 100) // 200


def _earned_day(o: Order) -> date | None:
    if o.delivered_on and o.paid_on and o.paid_cents >= o.net_total_cents:
        return o.paid_on if o.paid_on > o.delivered_on else o.delivered_on
    return None


def _earned_this_month(o: Order, month: str) -> bool:
    day = _earned_day(o)
    return (
        o.salesperson is not None
        and day is not None
        and f"{day:%Y-%m}" == month
        and not (o.refunded_on is not None and f"{o.refunded_on:%Y-%m}" == month)
    )


def reference_totals(
    orders: list[Order], plans: tuple[Plan, ...], eligible: set[str], month: str,
    prior_payouts: dict[str, dict[str, int]],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for person in sorted(eligible):
        mine = sorted(
            (o for o in orders if _earned_this_month(o, month) and person in o.split),
            key=lambda o: (_earned_day(o), o.order_id),
        )
        running, total = 0, 0
        for o in mine:
            plan = _plan_on(plans, o.created_on)
            net = _share(o.net_total_cents, o.split, person)
            room = max(0, plan.tier_threshold_cents - running)
            low, high = min(net, room), net - min(net, room)
            minus = plan.discount_penalty_bp if (
                o.discount_pct > plan.discount_threshold_pct and not o.discount_approved_by) else 0
            total += (low * (plan.base_rate_bp - minus) + high * (plan.tier_rate_bp - minus) + 5000) // 10000
            running += net
            mattresses = sum(i.qty for i in o.items
                             if i.brand == plan.promo_brand and i.category == plan.promo_category)
            total += _share(mattresses * plan.promo_bonus_cents, o.split, person)
        for o in orders:
            refunded_now = o.refunded_on is not None and f"{o.refunded_on:%Y-%m}" == month
            day = _earned_day(o)
            if refunded_now and day is not None and f"{day:%Y-%m}" < month:
                total -= prior_payouts.get(o.order_id, {}).get(person, 0)
        if total:
            totals[person] = total
    return totals
