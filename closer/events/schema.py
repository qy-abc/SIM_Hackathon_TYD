"""Event contract v0.1 -- the one schema every role builds against.

Every input to Closer (POS order, bank line, Telegram message, approval) becomes an
Event. Events are immutable; identity is the content hash, so replaying the same
input twice can never create a second event.

occurred_at MUST be the source record's own time (the till's order time, the bank line's
date, the Telegram message's timestamp), never the time we ingested it, and always
timezone-aware (Asia/Singapore). Otherwise a replay hashes differently and is stored twice.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_VERSION = "0.1"


class EventType(StrEnum):
    ORDER_PLACED = "OrderPlaced"
    ORDER_AMENDED = "OrderAmended"
    PAYMENT_SEEN = "PaymentSeen"
    SETTLEMENT_SEEN = "SettlementSeen"
    REFUND_SEEN = "RefundSeen"
    DELIVERY_COMPLETED = "DeliveryCompleted"
    DELIVERY_FAILED = "DeliveryFailed"
    RETURN_REQUESTED = "ReturnRequested"
    DISPUTE_RAISED = "DisputeRaised"
    PLAN_CHANGED = "PlanChanged"
    APPROVAL_GIVEN = "ApprovalGiven"
    APPROVAL_REJECTED = "ApprovalRejected"
    LEDGER_WRITTEN = "LedgerWritten"
    VERIFIED = "Verified"


# Keys a payload must carry. Extra keys are allowed; missing ones are rejected at the door.
REQUIRED_PAYLOAD_KEYS: dict[EventType, frozenset[str]] = {
    EventType.ORDER_PLACED: frozenset({"order_id", "customer_name", "net_total_cents", "salesperson"}),
    EventType.ORDER_AMENDED: frozenset({"order_id", "changes"}),
    EventType.PAYMENT_SEEN: frozenset({"reference", "amount_cents", "method"}),
    EventType.SETTLEMENT_SEEN: frozenset({"reference", "gross_cents", "fee_cents", "net_cents"}),
    EventType.REFUND_SEEN: frozenset({"order_id", "amount_cents"}),
    EventType.DELIVERY_COMPLETED: frozenset({"order_id"}),
    EventType.DELIVERY_FAILED: frozenset({"order_id", "reason"}),
    EventType.RETURN_REQUESTED: frozenset({"order_id"}),
    EventType.DISPUTE_RAISED: frozenset({"order_id", "raised_by", "claim"}),
    EventType.PLAN_CHANGED: frozenset({"plan_version", "effective_from"}),
    EventType.APPROVAL_GIVEN: frozenset({"approval_id", "approver"}),
    EventType.APPROVAL_REJECTED: frozenset({"approval_id", "approver"}),
    EventType.LEDGER_WRITTEN: frozenset({"entry_id", "approval_id"}),
    EventType.VERIFIED: frozenset({"entry_id", "matches"}),
}


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: EventType
    occurred_at: datetime
    source: str
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _validate(self) -> Event:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        missing = REQUIRED_PAYLOAD_KEYS[self.type] - self.payload.keys()
        if missing:
            raise ValueError(f"{self.type} payload missing {sorted(missing)}")
        return self

    @property
    def hash(self) -> str:
        canonical = json.dumps(
            [self.type.value, self.occurred_at.isoformat(), self.source, self.payload],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def event_id(self) -> str:
        return self.hash[:16]
