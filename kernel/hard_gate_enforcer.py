"""Hard-gate enforcement point — kernel-owned (spec §1, non-negotiable).

A ``hard_gate: true`` tool call is NEVER executed by the scheduler directly.
It emits ``task.needs_human`` naming the exact action and target, and blocks
until an explicit approval arrives through the Command Worker
(Telegram/Discord, Phase 4.5; until then, via the ``approve``/``deny`` API
used by tests and scripts).

There is deliberately **no mode, flag, or "trusted user" bypass** in this
module. Full-autonomous permission mode (spec §8) governs planning, retries,
and ``hard_gate: false`` tools only — it cannot reach in here. If a task
ever seems to require skipping the gate, the correct move is to surface a
``task.needs_human`` decision point, not to write a bypass. Do not add one.

Restart semantics: the approvals table is the source of truth and lives in
the same SQLite DB as everything else. A worker re-entering a gate after a
crash calls ``enforce`` again; a recorded decision is applied immediately,
a still-pending one keeps waiting. No approval is ever lost or duplicated.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .db import connect
from .event_bus import EventBus
from .task_graph import TaskGraph

log = logging.getLogger("kernel.hard_gate")

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    task_id    TEXT NOT NULL,
    gate_key   TEXT NOT NULL,
    decision   TEXT NOT NULL CHECK (decision IN ('approved','denied')),
    approver   TEXT,
    reason     TEXT,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    decided_at REAL NOT NULL,
    PRIMARY KEY (task_id, gate_key)
);
"""


class HardGateDenied(Exception):
    """Raised inside the worker when a human denies a gated action."""


@dataclass(frozen=True)
class ToolCallSpec:
    """A proposed tool call presented for gating.

    ``action`` and ``target`` must be concrete — they are what the human
    approves. "run metasploit module" is not acceptable;
    "run exploit/multi/handler against 10.0.0.5:4444" is.
    """
    tool_name: str
    action: str
    target: str
    hard_gate: bool
    args: dict = field(default_factory=dict)


class HardGateEnforcer:
    """The single enforcement point for spec §1. Thread-safe."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        poll_interval_s: float = 0.2,
        timeout_s: Optional[float] = None,
    ):
        self.db_path = str(db_path)
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        conn = connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.close()

    # ------------------------------------------------------------- gating

    def enforce(self, task_id: str, call: ToolCallSpec) -> None:
        """Check the gate. Returns immediately for ungated tools; blocks for
        gated ones until an explicit human decision exists.

        Raises HardGateDenied if the action was denied.
        """
        if not call.hard_gate:
            return  # read-only/analysis tool: no gate, nothing to record here

        if not call.action or not call.target:
            # The notification must name the specific action and target —
            # enforce that at the boundary, not in a prompt template.
            raise ValueError(
                "hard-gated calls must name a concrete action and target"
            )

        gate_key = self._gate_key(call)
        prior = self._decision(task_id, gate_key)
        if prior is None:
            # First time this gate is hit: mark the task and notify.
            graph = TaskGraph(self.db_path)
            graph.update_status(task_id, "needs_human")
            graph.close()
            bus = EventBus(self.db_path)
            bus.publish(
                "task.needs_human",
                {
                    "task_id": task_id,
                    "reason": "hard_gate",
                    "tool": call.tool_name,
                    "action": call.action,
                    "target": call.target,
                },
            )
            bus.close()
            log.warning(
                "hard gate: task %s waiting for approval: %s on %s",
                task_id, call.action, call.target,
            )

        decision = self._wait(task_id, gate_key)
        if decision == "approved":
            graph = TaskGraph(self.db_path)
            graph.update_status(task_id, "running")
            graph.close()
            return
        raise HardGateDenied(
            f"human denied gated action: {call.action} on {call.target}"
        )

    # ------------------------------------------------------- decision input

    def approve(self, task_id: str, call: ToolCallSpec, *, approver: str, reason: str = "") -> None:
        self._record(task_id, self._gate_key(call), "approved", approver, reason, call)

    def deny(self, task_id: str, call: ToolCallSpec, *, approver: str, reason: str = "") -> None:
        self._record(task_id, self._gate_key(call), "denied", approver, reason, call)

    def is_pending(self, task_id: str, call: ToolCallSpec) -> bool:
        return self._decision(task_id, self._gate_key(call)) is None

    # -------------------------------------------------------------- internals

    @staticmethod
    def _gate_key(call: ToolCallSpec) -> str:
        # The decision is bound to the exact action+target, not just the
        # tool: approving one target must not approve the same tool against
        # a different target.
        return f"{call.tool_name}|{call.action}|{call.target}"

    def _decision(self, task_id: str, gate_key: str) -> Optional[str]:
        conn = connect(self.db_path)
        row = conn.execute(
            "SELECT decision FROM approvals WHERE task_id = ? AND gate_key = ?",
            (task_id, gate_key),
        ).fetchone()
        conn.close()
        return row["decision"] if row else None

    def _record(self, task_id: str, gate_key: str, decision: str,
                approver: str, reason: str, call: ToolCallSpec) -> None:
        conn = connect(self.db_path)
        conn.execute(
            """INSERT INTO approvals
                 (task_id, gate_key, decision, approver, reason, action, target, decided_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(task_id, gate_key) DO UPDATE SET
                 decision = excluded.decision, approver = excluded.approver,
                 reason = excluded.reason, decided_at = excluded.decided_at""",
            (task_id, gate_key, decision, approver, reason,
             call.action, call.target, time.time()),
        )
        conn.commit()
        conn.close()
        log.info(
            "hard gate decision: task=%s action=%r target=%r decision=%s by=%s",
            task_id, call.action, call.target, decision, approver,
        )

    def _wait(self, task_id: str, gate_key: str) -> str:
        deadline = None if self.timeout_s is None else time.time() + self.timeout_s
        while True:
            decision = self._decision(task_id, gate_key)
            if decision is not None:
                return decision
            if deadline is not None and time.time() > deadline:
                raise TimeoutError(
                    f"hard gate approval wait timed out for task {task_id}"
                )
            time.sleep(self.poll_interval_s)
