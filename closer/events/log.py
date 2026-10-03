"""Append-only SQLite event log. UPDATE and DELETE are blocked by triggers."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path

from closer.events.schema import Event, EventType

_DDL = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    type        TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source      TEXT NOT NULL,
    payload     TEXT NOT NULL,
    hash        TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
"""


class EventLog:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.executescript(_DDL)

    def append(self, event: Event) -> bool:
        """Store the event. Returns False (and stores nothing) if it was already logged.

        Any other database error raises: a failed write must never pass as a duplicate.
        """
        if self._db.execute("SELECT 1 FROM events WHERE hash = ?", (event.hash,)).fetchone():
            return False
        with self._db:
            self._db.execute(
                "INSERT INTO events (event_id, type, occurred_at, source, payload, hash)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (event.event_id, event.type.value, event.occurred_at.isoformat(), event.source,
                 json.dumps(event.payload, sort_keys=True, default=str), event.hash),
            )
        return True

    def append_many(self, events: Iterable[Event]) -> int:
        return sum(self.append(e) for e in events)

    def read(self, since_seq: int = 0, types: Iterable[EventType] | None = None) -> Iterator[tuple[int, Event]]:
        sql = "SELECT seq, type, occurred_at, source, payload FROM events WHERE seq > ?"
        params: list[object] = [since_seq]
        if types is not None:
            wanted = [t.value for t in types]
            sql += f" AND type IN ({','.join('?' * len(wanted))})"
            params += wanted
        for seq, type_, at, source, payload in self._db.execute(sql + " ORDER BY seq", params):
            yield seq, Event(type=type_, occurred_at=datetime.fromisoformat(at),
                             source=source, payload=json.loads(payload))

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
