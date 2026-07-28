"""Shared SQLite connection helper for kernel state.

All kernel persistence (task graph, event bus, approvals) lives in a single
SQLite database file. WAL mode + a busy timeout give us safe concurrent
access from the asyncio scheduler thread and short-lived helper threads
(e.g. the hard-gate enforcer's approval waiter) without standing up any
external service — a hard requirement for a single-user 16GB-RAM machine.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "state/agent.db"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection configured for kernel use.

    - WAL journal mode: readers don't block writers across connections.
    - busy_timeout: wait instead of raising ``database is locked`` when the
      scheduler thread and an enforcer thread touch the DB at the same time.
    - Rows come back as ``sqlite3.Row`` for named access.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
