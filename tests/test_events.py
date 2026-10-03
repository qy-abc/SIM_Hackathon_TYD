import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from closer.events.log import EventLog
from closer.events.schema import REQUIRED_PAYLOAD_KEYS, Event, EventType

SGT = timezone(timedelta(hours=8))
AT = datetime(2026, 9, 3, 10, 15, tzinfo=SGT)


def order_placed(**payload) -> Event:
    base = {"order_id": "1001", "customer_name": "Tan Ah Kow", "net_total_cents": 189900, "salesperson": "S02"}
    return Event(type=EventType.ORDER_PLACED, occurred_at=AT, source="pos", payload={**base, **payload})


def test_every_event_type_declares_its_required_keys():
    assert set(REQUIRED_PAYLOAD_KEYS) == set(EventType)
    assert len(EventType) == 14


def test_rejects_payload_missing_required_keys():
    with pytest.raises(ValidationError, match="salesperson"):
        Event(type=EventType.ORDER_PLACED, occurred_at=AT, source="pos",
              payload={"order_id": "1001", "customer_name": "x", "net_total_cents": 1})


def test_rejects_naive_timestamps():
    with pytest.raises(ValidationError, match="timezone-aware"):
        Event(type=EventType.DELIVERY_COMPLETED, occurred_at=datetime(2026, 9, 3), source="telegram",
              payload={"order_id": "1001"})


def test_hash_is_stable_and_content_based():
    assert order_placed().hash == order_placed().hash
    assert order_placed().hash != order_placed(net_total_cents=1).hash
    assert order_placed().event_id == order_placed().hash[:16]


def test_replaying_the_same_event_stores_it_once():
    log = EventLog()
    assert log.append(order_placed()) is True
    assert log.append(order_placed()) is False
    assert log.count() == 1


def test_read_filters_by_type_and_sequence():
    log = EventLog()
    log.append(order_placed())
    log.append(Event(type=EventType.DELIVERY_FAILED, occurred_at=AT, source="telegram",
                     payload={"order_id": "1052", "reason": "customer not home"}))
    failed = list(log.read(types=[EventType.DELIVERY_FAILED]))
    assert [e.payload["order_id"] for _, e in failed] == ["1052"]
    assert [seq for seq, _ in log.read(since_seq=1)] == [2]


def test_log_is_append_only(tmp_path):
    log = EventLog(tmp_path / "events.db")
    log.append(order_placed())
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._db.execute("UPDATE events SET source = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._db.execute("DELETE FROM events")
