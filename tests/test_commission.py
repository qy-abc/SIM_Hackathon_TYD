"""Hand-worked cases for the six rules. Each expected number is worked out in the comment."""
from datetime import date

import pytest

from closer.rules.commission import allocate, compute_month
from tests.conftest import SLUMBER, make_order

MONTH = "2026-09"


def totals(orders, plans, staff, prior=None, disputes=None):
    return compute_month(orders, plans, staff, MONTH, prior, open_disputes=disputes).totals()


def test_rule1_base_rate(plans, staff):
    # S$1,000 x 3% = S$30.00
    assert totals([make_order(net=100000)], plans, staff) == {"S04": 3000}


def test_rule1_tier_applies_only_above_threshold(plans, staff):
    # S$29,000 then S$2,000: 29,000x3% + 1,000x3% + 1,000x5% = 870 + 30 + 50 = S$950
    orders = [make_order("1", net=2900000, delivered_on=date(2026, 9, 5)),
              make_order("2", net=200000, delivered_on=date(2026, 9, 6))]
    assert totals(orders, plans, staff) == {"S04": 95000}


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
    assert totals([order], plans, staff) == {"S04": 3000}


def test_rule3_split_deal(plans, staff):
    # S$1,000 split 60/40: S$600 x 3% = 18.00, S$400 x 3% = 12.00
    order = make_order(split={"S04": 60, "S06": 40})
    assert totals([order], plans, staff) == {"S04": 1800, "S06": 1200}


def test_rule3_ineligible_staff_earn_nothing(plans, staff):
    # S02 (Jason, sales_manager) is not commission_eligible; only S04 earns.
    assert totals([make_order(split={"S02": 50, "S04": 50})], plans, staff) == {"S04": 1500}


def test_rule4_unapproved_discount_costs_one_point(plans, staff):
    # 14% off, no approver: S$1,000 x 2% = S$20.00
    assert totals([make_order(discount_pct=14)], plans, staff) == {"S04": 2000}


def test_rule4_approved_discount_has_no_penalty(plans, staff):
    assert totals([make_order(discount_pct=14, discount_approved_by="Jason")], plans, staff) == {"S04": 3000}


def test_rule4_ten_percent_is_not_over_the_line(plans, staff):
    assert totals([make_order(discount_pct=10)], plans, staff) == {"S04": 3000}


def test_rule5_promo_bonus_follows_plan_on_order_date(plans, staff):
    # Before 15 Sep: S$1,000 x 3% + S$50 = 80.00. From 15 Sep: S$30 + S$80 = 110.00
    before = make_order("1", items=(SLUMBER,), created_on=date(2026, 9, 14))
    after = make_order("2", items=(SLUMBER,), created_on=date(2026, 9, 15), delivered_on=date(2026, 9, 20))
    assert totals([before], plans, staff) == {"S04": 8000}
    assert totals([after], plans, staff) == {"S04": 11000}


def test_rule6_clawback_on_order_paid_out_last_month(plans, staff):
    order = make_order("0987", delivered_on=date(2026, 8, 25), paid_on=date(2026, 8, 20),
                       refunded_on=date(2026, 9, 10))
    result = compute_month([order], plans, staff, MONTH, {"0987": {"S04": 3000}})
    assert [(line.kind, line.amount_cents) for line in result.lines] == [("clawback", -3000)]


def test_rule6_refund_with_no_payout_on_record_is_held_not_ignored(plans, staff):
    order = make_order("0987", delivered_on=date(2026, 8, 25), paid_on=date(2026, 8, 20),
                       refunded_on=date(2026, 9, 10))
    result = compute_month([order], plans, staff, MONTH, {})
    assert result.lines == ()
    assert [h.reason for h in result.holds] == ["refunded, but no earlier payout on record to claw back"]


def test_rounding_is_half_up_per_line(plans, staff):
    # S$0.50 x 3% = 1.5 cents -> 2 cents
    assert totals([make_order(net=50)], plans, staff) == {"S04": 2}


def test_allocate_keeps_every_cent():
    assert allocate(100001, {"S04": 60, "S06": 40}) == {"S04": 60001, "S06": 40000}
    with pytest.raises(ValueError):
        allocate(100, {"S04": 60, "S06": 30})


# ---------------------------------------------------------------------------
# New rule tests (disputes, missing salesperson, GST)
# ---------------------------------------------------------------------------

def test_dispute_order_is_held_with_reason(plans, staff):
    # Order 1043 is in open_disputes → Hold(reason="dispute open"), no commission line.
    # net=100_000, gst_rate_bp=0 (plans fixture), rate 3% would give 3_000 — but the
    # order never reaches commission calculation because it is intercepted first.
    result = compute_month([make_order("1043")], plans, staff, MONTH,
                           open_disputes={"1043"})
    assert result.lines == ()
    assert len(result.holds) == 1
    assert result.holds[0].order_id == "1043"
    assert result.holds[0].reason == "dispute open"


def test_missing_salesperson_order_is_held(plans, staff):
    # Roadshow order with salesperson=None and split={} → held, no commission.
    # The hold_reason path catches it: "no salesperson recorded".
    order = make_order(salesperson=None, split={})
    result = compute_month([order], plans, staff, MONTH)
    assert result.lines == ()
    assert len(result.holds) == 1
    assert result.holds[0].reason == "no salesperson recorded"


def test_gst_base_strips_tax_before_commission(plans_gst, staff):
    # net_total_cents = 109_000 (S$1,090 incl. 9% GST = 900 bp).
    # GST-exclusive base = round_half_up(109_000 × 10_000 / (10_000 + 900))
    #   = round_half_up(109_000 × 10_000 / 10_900)
    #   = (1_090_000_000 + 5_450) // 10_900          <- (numerator + denom//2) // denom
    #   = 1_090_005_450 // 10_900 = 100_000 exactly  <- 10_900 × 100_000 = 1_090_000_000
    # Commission = round_half_up(100_000 × 300) = (30_000_000 + 5_000) // 10_000 = 3_000 ✓
    # Compare with gst_rate_bp=0: base = 109_000, commission = (109_000×300+5_000)//10_000
    #   = (32_700_000+5_000)//10_000 = 3_270  ← different, confirming GST matters.
    assert totals([make_order(net=109000)], plans_gst, staff) == {"S04": 3000}
