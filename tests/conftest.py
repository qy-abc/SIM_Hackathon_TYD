from datetime import date

import pytest

from closer.data.models import LineItem, Order, Staff
from closer.rules.plan import load_plans

SOFA = LineItem(sku="SF-OS3", name="Oslo Sofa", category="sofa", brand="Nordhaus", qty=1, unit_cents=200000)
SLUMBER = LineItem(sku="MT-SLQ", name="Slumber Queen", category="mattress", brand="Slumber", qty=1, unit_cents=100000)


def make_order(order_id="1", net=100000, **overrides) -> Order:
    fields = dict(
        order_id=order_id, source="pos", created_on=date(2026, 9, 2), customer_name="Tan Ah Kow",
        phone="91234567", items=(SOFA,), list_total_cents=net, discount_pct=0, discount_approved_by=None,
        net_total_cents=net, salesperson="S02", split={"S02": 100}, payment_method="PayNow",
        paid_cents=net, paid_on=date(2026, 9, 2), delivered_on=date(2026, 9, 5),
    )
    return Order(**{**fields, **overrides})


@pytest.fixture
def plans():
    return load_plans()


@pytest.fixture
def staff():
    return (
        Staff(staff_id="S01", name="Jason", role="owner", telegram_id="t1", commission_eligible=False),
        Staff(staff_id="S02", name="Ravi", role="salesperson", telegram_id="t2", commission_eligible=True),
        Staff(staff_id="S04", name="Daniel", role="salesperson", telegram_id="t4", commission_eligible=True),
    )
