"""Scheduler — kernel-owned (spec §2.1).

An always-running loop, not a conversational turn-taker. Each iteration:
promote dependency-satisfied tasks, pop a ``ready`` task, dispatch it to a
worker, apply the result (children, artifacts, summary) or hand the failure
to the Recovery Manager.

Kernel/service boundary (spec §2): the worker is a *service* the kernel
dispatches to. Every dispatch goes through a timeout + exception boundary
that converts a service failure into recovery handling — a worker crash can
never kill the scheduler loop.

Restart property (Phase 1 acceptance): kill the process mid-task, restart,
and ``recover_interrupted`` re-queues the interrupted task; idempotent
workers resume without any user re-prompting. Tested in
tests/test_scheduler_resume.py.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .event_bus import EventBus
from .recovery import RecoveryAction, RecoveryManager
from .task_graph import TaskGraph, TaskNode

log = logging.getLogger("kernel.scheduler")


@dataclass
class SubtaskSpec:
    """A child task the worker asks the scheduler to spawn."""
    goal: str
    depends_on: list[str] = field(default_factory=list)
    assigned_agent: Optional[str] = None


@dataclass
class WorkerResult:
    result_summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    subtasks: list[SubtaskSpec] = field(default_factory=list)
    full_log_ref: Optional[str] = None


class WorkerContext:
    """What the kernel hands a worker. Services touch the kernel only
    through this surface (plus the tool registry / enforcer later)."""

    def __init__(self, task: TaskNode, graph: TaskGraph, bus: EventBus):
        self.task = task
        self.graph = graph
        self.bus = bus

    def emit_progress(self, message: str, **extra: Any) -> None:
        payload = {"task_id": self.task.id, "message": message}
        payload.update(extra)
        self.bus.publish("task.progress", payload)


# Worker contract: async fn(task, ctx) -> WorkerResult.
WorkerFn = Callable[[TaskNode, WorkerContext], Awaitable[WorkerResult]]


class Scheduler:
    def __init__(
        self,
        graph: TaskGraph,
        bus: EventBus,
        worker: WorkerFn,
        *,
        poll_interval_s: float = 0.25,
        task_timeout_s: float = 300.0,
        recovery: Optional[RecoveryManager] = None,
        max_concurrency: int = 4,
    ):
        self.graph = graph
        self.bus = bus
        self.worker = worker
        self.poll_interval_s = poll_interval_s
        self.task_timeout_s = task_timeout_s
        self.recovery = recovery or RecoveryManager()
        self.max_concurrency = max_concurrency
        self._inflight: set[asyncio.Task] = set()
        self._stopped = asyncio.Event()

    # ------------------------------------------------------------ main loop

    def stop(self) -> None:
        self._stopped.set()

    async def run_forever(self) -> None:
        """Daemon loop (systemd/long-lived asyncio task). Ctrl-C / stop() to end."""
        self.graph.recover_interrupted()
        while not self._stopped.is_set():
            await self._pump()
            await asyncio.sleep(self.poll_interval_s)

    async def run_until_idle(self, *, timeout_s: float = 60.0) -> None:
        """Run until no schedulable work remains. Used by tests and demos."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        self.graph.recover_interrupted()
        while True:
            await self._pump()
            if not self._inflight and not self.graph.has_active_work():
                return
            if loop.time() > deadline:
                raise TimeoutError(
                    "scheduler did not go idle within "
                    f"{timeout_s}s (inflight={len(self._inflight)})"
                )
            await asyncio.sleep(self.poll_interval_s)

    async def _pump(self) -> None:
        self.graph.refresh_ready()
        while len(self._inflight) < self.max_concurrency:
            node = self.graph.pop_ready()
            if node is None:
                break
            self._spawn(node)
        if self._inflight:
            done, _ = await asyncio.wait(
                self._inflight,
                timeout=self.poll_interval_s,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                self._inflight.discard(t)
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc is not None:
                    # _dispatch has its own boundary; reaching here means a
                    # bug in the boundary itself. Log loudly, keep the loop.
                    log.exception("dispatch boundary leaked: %r", exc)

    # -------------------------------------------------------------- dispatch

    def _spawn(self, node: TaskNode) -> None:
        self._inflight.add(asyncio.ensure_future(self._dispatch(node)))

    async def _dispatch(self, node: TaskNode) -> None:
        """The kernel->service boundary. Never raises."""
        attempt = node.retry_count + 1
        self.bus.publish(
            "task.started",
            {"task_id": node.id, "goal": node.goal, "attempt": attempt},
        )
        ctx = WorkerContext(node, self.graph, self.bus)
        try:
            result = await asyncio.wait_for(
                self.worker(node, ctx), timeout=self.task_timeout_s
            )
        except Exception as exc:  # noqa: BLE001 — boundary, by design
            await self._handle_failure(node, exc)
            return
        self._apply_result(node, result)

    def _apply_result(self, node: TaskNode, result: WorkerResult) -> None:
        self.graph.update_status(
            node.id,
            "done",
            result_summary=result.result_summary,
            artifacts=result.artifacts,
            full_log_ref=result.full_log_ref,
        )
        for spec in result.subtasks:
            self.graph.add_task(
                spec.goal,
                parent_id=node.id,
                depends_on=spec.depends_on,
                assigned_agent=spec.assigned_agent,
            )
        self.bus.publish(
            "task.done",
            {
                "task_id": node.id,
                "result_summary": result.result_summary,
                "artifacts": list(result.artifacts),
                "subtasks_spawned": len(result.subtasks),
            },
        )

    async def _handle_failure(self, node: TaskNode, exc: BaseException) -> None:
        decision = self.recovery.handle(node, exc)
        if decision.action is RecoveryAction.RETRY:
            retry_count = self.graph.increment_retry(node.id)
            self.bus.publish(
                "task.progress",
                {
                    "task_id": node.id,
                    "message": (
                        f"transient failure, retry {retry_count} scheduled "
                        f"in {decision.delay_s:.1f}s: {decision.reason}"
                    ),
                },
            )
            if decision.delay_s > 0:
                await asyncio.sleep(decision.delay_s)
            self.graph.update_status(node.id, "ready")
            return

        # Terminal (Phase 1): mark failed, block dependents, notify.
        self.graph.update_status(node.id, "failed", result_summary=decision.reason)
        blocked = self.graph.mark_blocked_dependents(node.id)
        self.bus.publish(
            "task.failed",
            {
                "task_id": node.id,
                "error": decision.reason,
                "failure_class": decision.failure_class.value,
                "retry_count": node.retry_count,
                "terminal": True,
                "dependents_blocked": blocked,
            },
        )
