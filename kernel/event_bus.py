"""Event Bus — kernel-owned (spec §2.2).

Durable, SQLite-outbox event bus. Chosen deliberately over Redis pub/sub:
on a single-user machine the outbox gives at-least-once delivery *for free*
because events and consumer offsets live in the same database as the task
graph — an event surviving a bus restart is not optional, it just happens.

Guarantees:
- **At-least-once**: a consumer's offset advances (``ack``) only after its
  handler returns successfully. Crash before ack -> redelivery.
- **Fan-out**: every consumer has its own offset, so every consumer sees
  every event (notifier, recovery triggers, memory compression, plugins).
- **Dead-letter**: after ``max_attempts`` failures for one event, the event
  is moved to ``dead_letters`` with the error recorded, and the offset is
  advanced past it so one poison event cannot stall a consumer forever.
- **Replay**: events are durable, so ``replay(since=...)`` answers
  "what actually happened during that run" without grepping logs.

Schema versioning: every event carries ``schema_version``. Add new fields
by extending payloads; never repurpose an existing field's meaning. The
current version per event type lives in ``EVENT_SCHEMA`` below and in
docs/event_schema.md — keep the two in sync.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .db import connect

log = logging.getLogger("kernel.event_bus")

# Current schema version per known event type. Unknown types are accepted at
# version 1 (forward compatibility: old consumers must ignore unknown types).
EVENT_SCHEMA: dict[str, int] = {
    "task.started": 1,
    "task.progress": 1,
    "task.needs_human": 1,
    "task.done": 1,
    "task.failed": 1,
    "budget.warning": 1,
    "plugin.loaded": 1,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload        TEXT NOT NULL DEFAULT '{}',
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE TABLE IF NOT EXISTS consumer_offsets (
    consumer      TEXT PRIMARY KEY,
    last_event_id INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS event_attempts (
    consumer   TEXT NOT NULL,
    event_id   INTEGER NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (consumer, event_id)
);
CREATE TABLE IF NOT EXISTS dead_letters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    consumer   TEXT NOT NULL,
    event_id   INTEGER NOT NULL,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    error      TEXT,
    failed_at  REAL NOT NULL
);
"""


@dataclass
class Event:
    id: int
    type: str
    schema_version: int
    payload: dict[str, Any]
    created_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        return cls(
            id=int(row["id"]),
            type=row["type"],
            schema_version=int(row["schema_version"]),
            payload=json.loads(row["payload"]),
            created_at=float(row["created_at"]),
        )


@dataclass
class DeadLetter:
    consumer: str
    event_id: int
    type: str
    payload: dict[str, Any]
    error: Optional[str]
    failed_at: float


class EventBus:
    """One instance per thread. Shares the SQLite file with the task graph."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = connect(self.db_path)
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------ publishing

    def publish(
        self,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        schema_version: Optional[int] = None,
    ) -> int:
        version = schema_version or EVENT_SCHEMA.get(event_type, 1)
        cur = self._conn.execute(
            "INSERT INTO events (type, schema_version, payload, created_at) VALUES (?,?,?,?)",
            (event_type, version, json.dumps(payload or {}), time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # ------------------------------------------------------------ consuming

    def _offset(self, consumer: str) -> int:
        row = self._conn.execute(
            "SELECT last_event_id FROM consumer_offsets WHERE consumer = ?", (consumer,)
        ).fetchone()
        return int(row["last_event_id"]) if row else 0

    def pending(self, consumer: str, *, limit: int = 100) -> list[Event]:
        """Events this consumer has not yet acked, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?",
            (self._offset(consumer), limit),
        ).fetchall()
        return [Event.from_row(r) for r in rows]

    def ack(self, consumer: str, event_id: int) -> None:
        """Advance the consumer offset. Only call after successful handling."""
        self._conn.execute(
            """INSERT INTO consumer_offsets (consumer, last_event_id, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(consumer) DO UPDATE SET
                 last_event_id = MAX(last_event_id, excluded.last_event_id),
                 updated_at    = excluded.updated_at""",
            (consumer, event_id, time.time()),
        )
        self._conn.execute(
            "DELETE FROM event_attempts WHERE consumer = ? AND event_id = ?",
            (consumer, event_id),
        )
        self._conn.commit()

    def _record_failure(self, consumer: str, event: Event, error: str) -> int:
        self._conn.execute(
            """INSERT INTO event_attempts (consumer, event_id, attempts, last_error)
               VALUES (?,?,1,?)
               ON CONFLICT(consumer, event_id) DO UPDATE SET
                 attempts = attempts + 1, last_error = excluded.last_error""",
            (consumer, event.id, error),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT attempts FROM event_attempts WHERE consumer = ? AND event_id = ?",
            (consumer, event.id),
        ).fetchone()
        return int(row["attempts"])

    def _dead_letter(self, consumer: str, event: Event, error: str) -> None:
        self._conn.execute(
            """INSERT INTO dead_letters (consumer, event_id, type, payload, error, failed_at)
               VALUES (?,?,?,?,?,?)""",
            (consumer, event.id, event.type, json.dumps(event.payload), error, time.time()),
        )
        self._conn.commit()
        # Alert, don't drop silently: the row is the durable record, the log
        # line is what a human/watchdog sees today. A notifier consumer for
        # dead letters is planned with the notify layer (Phase 4.5).
        log.error(
            "event dead-lettered: consumer=%s event_id=%s type=%s error=%s",
            consumer, event.id, event.type, error,
        )

    def dispatch(
        self,
        consumer: str,
        handler: Callable[[Event], None],
        *,
        max_attempts: int = 5,
        limit: int = 100,
    ) -> int:
        """Feed pending events to ``handler`` with at-least-once semantics.

        Returns the number of events successfully handled this call.
        A handler that raises gets retried on a later dispatch; after
        ``max_attempts`` failures the event is dead-lettered and skipped.
        """
        handled = 0
        for event in self.pending(consumer, limit=limit):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 — must not kill the loop
                attempts = self._record_failure(consumer, event, repr(exc))
                if attempts >= max_attempts:
                    self._dead_letter(consumer, event, repr(exc))
                    self.ack(consumer, event.id)  # move past the poison event
                continue
            self.ack(consumer, event.id)
            handled += 1
        return handled

    # ---------------------------------------------------------------- replay

    def replay(
        self,
        *,
        since: Optional[float] = None,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> list[Event]:
        """Return stored events for debugging: 'what actually happened'."""
        query = "SELECT * FROM events WHERE 1=1"
        args: list[Any] = []
        if since is not None:
            query += " AND created_at >= ?"
            args.append(since)
        if event_type is not None:
            query += " AND type = ?"
            args.append(event_type)
        query += " ORDER BY id LIMIT ?"
        args.append(limit)
        return [Event.from_row(r) for r in self._conn.execute(query, args)]

    def dead_letters(self) -> list[DeadLetter]:
        rows = self._conn.execute(
            "SELECT * FROM dead_letters ORDER BY id"
        ).fetchall()
        return [
            DeadLetter(
                consumer=r["consumer"],
                event_id=int(r["event_id"]),
                type=r["type"],
                payload=json.loads(r["payload"]),
                error=r["error"],
                failed_at=float(r["failed_at"]),
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
