"""Task Graph persistence — kernel-owned (spec §2.1).

The task graph is the single source of truth for *what the agent is doing*.
It is persisted in SQLite so the scheduler can be killed mid-task and resume
from the graph with no user re-prompting (Phase 1 acceptance test).

Design notes:
- ``depends_on`` holds a JSON list of task ids; a ``pending`` task is
  promoted to ``ready`` only when every dependency is ``done``.
- ``result_summary`` is the short string a child reports to its parent;
  ``full_log_ref`` points at an external log and is never loaded into
  context by default (memory system, Phase 2, builds on this).
- ``needs_human`` is a real persisted status, not an in-memory pause: an
  approval wait survives a process restart (see hard_gate_enforcer.py).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .db import connect

VALID_STATUSES = frozenset(
    {"pending", "ready", "running", "blocked", "needs_human", "done", "failed"}
)
TERMINAL_STATUSES = frozenset({"done", "failed", "blocked"})
ACTIVE_STATUSES = frozenset({"pending", "ready", "running"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    parent_id     TEXT,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    assigned_agent TEXT,
    depends_on    TEXT NOT NULL DEFAULT '[]',
    artifacts     TEXT NOT NULL DEFAULT '[]',
    result_summary TEXT,
    full_log_ref  TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    CHECK (status IN ('pending','ready','running','blocked','needs_human','done','failed'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent  ON tasks(parent_id);
"""


@dataclass
class TaskNode:
    id: str
    goal: str
    parent_id: Optional[str] = None
    status: str = "pending"
    assigned_agent: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    result_summary: Optional[str] = None
    full_log_ref: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    retry_count: int = 0

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TaskNode":
        return cls(
            id=row["id"],
            parent_id=row["parent_id"],
            goal=row["goal"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            depends_on=json.loads(row["depends_on"]),
            artifacts=json.loads(row["artifacts"]),
            result_summary=row["result_summary"],
            full_log_ref=row["full_log_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            retry_count=row["retry_count"],
        )


class TaskGraph:
    """SQLite-backed task graph. One instance per thread."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._conn = connect(self.db_path)
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ CRUD

    def add_task(
        self,
        goal: str,
        *,
        parent_id: Optional[str] = None,
        depends_on: Optional[Iterable[str]] = None,
        assigned_agent: Optional[str] = None,
    ) -> TaskNode:
        deps = list(depends_on or [])
        now = time.time()
        node = TaskNode(
            id=TaskNode.new_id(),
            goal=goal,
            parent_id=parent_id,
            status="ready" if not deps else "pending",
            assigned_agent=assigned_agent,
            depends_on=deps,
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            """INSERT INTO tasks
               (id, parent_id, goal, status, assigned_agent, depends_on,
                artifacts, created_at, updated_at, retry_count)
               VALUES (?,?,?,?,?,?,?,?,?,0)""",
            (
                node.id,
                node.parent_id,
                node.goal,
                node.status,
                node.assigned_agent,
                json.dumps(node.depends_on),
                json.dumps(node.artifacts),
                node.created_at,
                node.updated_at,
            ),
        )
        self._conn.commit()
        return node

    def get(self, task_id: str) -> Optional[TaskNode]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return TaskNode.from_row(row) if row else None

    def all_tasks(self) -> list[TaskNode]:
        rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        return [TaskNode.from_row(r) for r in rows]

    def children(self, parent_id: str) -> list[TaskNode]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at", (parent_id,)
        ).fetchall()
        return [TaskNode.from_row(r) for r in rows]

    # --------------------------------------------------------------- status

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        result_summary: Optional[str] = None,
        artifacts: Optional[Iterable[str]] = None,
        full_log_ref: Optional[str] = None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        sets = ["status = ?", "updated_at = ?"]
        args: list[Any] = [status, time.time()]
        if result_summary is not None:
            sets.append("result_summary = ?")
            args.append(result_summary)
        if artifacts is not None:
            sets.append("artifacts = ?")
            args.append(json.dumps(list(artifacts)))
        if full_log_ref is not None:
            sets.append("full_log_ref = ?")
            args.append(full_log_ref)
        args.append(task_id)
        self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args)
        self._conn.commit()

    def increment_retry(self, task_id: str) -> int:
        self._conn.execute(
            "UPDATE tasks SET retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
            (time.time(), task_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT retry_count FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return int(row["retry_count"])

    # ------------------------------------------------------------ scheduling

    def refresh_ready(self) -> list[TaskNode]:
        """Promote pending tasks whose dependencies are all done to ready."""
        done_ids = {
            r["id"] for r in self._conn.execute("SELECT id FROM tasks WHERE status = 'done'")
        }
        promoted: list[TaskNode] = []
        for row in self._conn.execute("SELECT * FROM tasks WHERE status = 'pending'"):
            node = TaskNode.from_row(row)
            if all(dep in done_ids for dep in node.depends_on):
                self.update_status(node.id, "ready")
                node.status = "ready"
                promoted.append(node)
        return promoted

    def pop_ready(self) -> Optional[TaskNode]:
        """Atomically take the oldest ready task and mark it running."""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE status = 'ready' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        self.update_status(row["id"], "running")
        node = TaskNode.from_row(row)
        node.status = "running"
        return node

    def recover_interrupted(self) -> list[TaskNode]:
        """Re-queue work left dangling by a crash/kill.

        - ``running`` tasks are put back to ``ready`` (the worker they were
          dispatched to died with the process; workers must be idempotent).
        - ``needs_human`` tasks: if an approval was already recorded before
          the crash, re-queue so the worker re-enters and proceeds; if a
          denial was recorded, re-queue so the worker surfaces the denial
          through the normal failure path. If the decision is still pending,
          the task is also re-queued — the re-dispatched worker will re-enter
          the gate and keep waiting. The approvals table is the source of
          truth either way, so no decision is ever lost.
        """
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status IN ('running','needs_human')"
        ).fetchall()
        recovered: list[TaskNode] = []
        for row in rows:
            self.update_status(row["id"], "ready")
            node = TaskNode.from_row(row)
            node.status = "ready"
            recovered.append(node)
        return recovered

    def mark_blocked_dependents(self, failed_task_id: str) -> list[str]:
        """Recursively mark dependents of a terminally-failed task blocked."""
        blocked: list[str] = []
        frontier = [failed_task_id]
        while frontier:
            current = frontier.pop()
            for row in self._conn.execute("SELECT * FROM tasks"):
                node = TaskNode.from_row(row)
                if current in node.depends_on and node.status not in TERMINAL_STATUSES:
                    self.update_status(node.id, "blocked")
                    blocked.append(node.id)
                    frontier.append(node.id)
        return blocked

    def has_active_work(self) -> bool:
        """True while any task is pending/ready/running (i.e. schedulable)."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('pending','ready','running')"
        ).fetchone()
        return int(row["n"]) > 0

    def close(self) -> None:
        self._conn.close()
