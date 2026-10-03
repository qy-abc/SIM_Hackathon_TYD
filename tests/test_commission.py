"""Hand-worked cases for the six rules. Each expected number is worked out in the comment."""
from datetime import date

import pytest

from closer.rules.commission import allocate, compute_month
from tests.conftest import SLUMBER, make_order

MONTH = "2026-09"


def totals(orders, plans, staff, prior=None):
    return compute_month(orders, plans, staff, MONTH, prior).totals()


def test_rule1_base_rate(plans, staff):
    # S$1,000 x 3% = S$30.00
    assert totals([make_order(net=100000)], plans, staff) == {"S02": 3000}


def test_rule1_tier_applies_only_above_threshold(plans, staff):
    # S$29,000 then S$2,000: 29,000x3% + 1,000x3% + 1,000x5% = 870 + 30 + 50 = S$950
    orders = [make_order("1", net=2900000, delivered_on=date(2026, 9, 5)),
              make_order("2", net=200000, delivered_on=date(2026, 9, 6))]
    assert totals(orders, plans, staff) == {"S02": 95000}


@pytest.mark.parametrize("override, reason", [
    ({"delivered_on": None}, "not delivered this month"),
    ({"delivered_on": date(2026, 10, 2)}, "not delivered this month"),
    ({"paid_cents": 30000, "paid_on": None}, "balance due S$700.00"),
    ({"paid_on": date(2026, 10, 3)}, "paid in full after this month"),
    ({"delivered_on": date(2026, 8, 28), "paid_on": date(2026, 8, 20)}, "earned in an earlier month"),
    ({"salesperson": None}, "no salesperson recorded"),
    ({"refunded_on": date(2026, 9, 15)}, "refunded in the month it was earned"),
])
def test_rule2_unearned_orders_are_held_with_a_reason(plans, staff, override, reason):
    result = compute_month([make_order(**override)], plans, staff, MONTH)
    assert result.lines == ()
    assert [h.reason for h in result.holds] == [reason]


def test_rule2_delivered_last_month_paid_off_this_month_earns_now(plans, staff):
    order = make_order(delivered_on=date(2026, 8, 28), paid_on=date(2026, 9, 4))
    assert totals([order], plans, staff) == {"S02": 3000}


def test_rule3_split_deal(plans, staff):
    # S$1,000 split 60/40: S$600 x 3% = 18.00, S$400 x 3% = 12.00
    order = make_order(split={"S02": 60, "S04": 40})
    assert totals([order], plans, staff) == {"S02": 1800, "S04": 1200}


def test_rule3_ineligible_staff_earn_nothing(plans, staff):
    assert totals([make_order(split={"S01": 50, "S02": 50})], plans, staff) == {"S02": 1500}


def test_rule4_unapproved_discount_costs_one_point(plans, staff):
    # 14% off, no approver: S$1,000 x 2% = S$20.00
    assert totals([make_order(discount_pct=14)], plans, staff) == {"S02": 2000}


def test_rule4_approved_discount_has_no_penalty(plans, staff):
    assert totals([make_order(discount_pct=14, discount_approved_by="Jason Lim")], plans, staff) == {"S02": 3000}


def test_rule4_ten_percent_is_not_over_the_line(plans, staff):
    assert totals([make_order(discount_pct=10)], plans, staff) == {"S02": 3000}


def test_rule5_promo_bonus_follows_plan_on_order_date(plans, staff):
    # Before 15 Sep: S$1,000 x 3% + S$50 = 80.00. From 15 Sep: S$30 + S$80 = 110.00
    before = make_order("1", items=(SLUMBER,), created_on=date(2026, 9, 14))
    after = make_order("2", items=(SLUMBER,), created_on=date(2026, 9, 15), delivered_on=date(2026, 9, 20))
    assert totals([before], plans, staff) == {"S02": 8000}
    assert totals([after], plans, staff) == {"S02": 11000}


def test_rule6_clawback_on_order_paid_out_last_month(plans, staff):
    order = make_order("0987", delivered_on=date(2026, 8, 25), paid_on=date(2026, 8, 20),
                       refunded_on=date(2026, 9, 10))
    result = compute_month([order], plans, staff, MONTH, {"0987": {"S02": 3000}})
    assert [(line.kind, line.amount_cents) for line in result.lines] == [("clawback", -3000)]


def test_rule6_refund_with_no_payout_on_record_is_held_not_ignored(plans, staff):
    order = make_order("0987", delivered_on=date(2026, 8, 25), paid_on=date(2026, 8, 20),
                       refunded_on=date(2026, 9, 10))
    result = compute_month([order], plans, staff, MONTH, {})
    assert result.lines == ()
    assert [h.reason for h in result.holds] == ["refunded, but no earlier payout on record to claw back"]


def test_rounding_is_half_up_per_line(plans, staff):
    # S$0.50 x 3% = 1.5 cents -> 2 cents
    assert totals([make_order(net=50)], plans, staff) == {"S02": 2}


def test_allocate_keeps_every_cent():
    assert allocate(100001, {"S02": 60, "S04": 40}) == {"S02": 60001, "S04": 40000}
    with pytest.raises(ValueError):
        allocate(100, {"S02": 60, "S04": 30})
