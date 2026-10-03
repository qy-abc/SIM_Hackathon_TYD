"""Domain records shared by every role. Money is always integer cents."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class LineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    name: str
    category: str
    brand: str
    qty: int
    unit_cents: int


class Order(BaseModel):
    """A golden order: one real sale after duplicates are merged and blanks resolved."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    source: str  # "pos" or "roadshow"
    created_on: date
    customer_name: str
    phone: str
    items: tuple[LineItem, ...]
    list_total_cents: int
    discount_pct: int
    discount_approved_by: str | None
    net_total_cents: int
    salesperson: str | None
    split: dict[str, int]  # staff_id -> percent, sums to 100
    payment_method: str
    paid_cents: int
    paid_on: date | None  # the day the balance reached zero; None while anything is owed
    delivered_on: date | None
    refunded_on: date | None = None

    @property
    def earned_on(self) -> date | None:
        """Rule 2: the later of fully paid and delivered."""
        if self.paid_on is None or self.delivered_on is None or self.paid_cents < self.net_total_cents:
            return None
        return max(self.paid_on, self.delivered_on)


class Staff(BaseModel):
    model_config = ConfigDict(frozen=True)

    staff_id: str
    name: str
    role: str
    telegram_id: str
    commission_eligible: bool
