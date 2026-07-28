"""Recovery Manager + scheduler failure-path tests (spec §2.3, Phase 1 scope).

- transient failure -> retried with backoff -> succeeds (different path than
  terminal failure)
- exhausted transient retries -> terminal failed, dependents blocked
- unknown worker exception -> classified transient, never kills the loop
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from kernel.event_bus import EventBus
from kernel.recovery import (
    EnvironmentFailure,
    FailureClass,
    LogicError,
    RecoveryAction,
    RecoveryManager,
    TransientError,
)
from kernel.scheduler import Scheduler, WorkerResult
from kernel.task_graph import TaskGraph, TaskNode


class RecoveryPolicyTest(unittest.TestCase):
    def setUp(self):
        self.recovery = RecoveryManager(max_retries=2, base_backoff_s=0.01)

    def _node(self, retry_count=0):
        return TaskNode(id="t1", goal="g", retry_count=retry_count)

    def test_classify_marker_exceptions(self):
        self.assertIs(self.recovery.classify(TransientError("blip")),
                      FailureClass.TRANSIENT)
        self.assertIs(self.recovery.classify(LogicError("bad plan")),
                      FailureClass.LOGIC)
        self.assertIs(self.recovery.classify(EnvironmentFailure("no disk")),
                      FailureClass.ENVIRONMENT)
        self.assertIs(self.recovery.classify(ValueError("???")),
                      FailureClass.TRANSIENT)

    def test_transient_retries_until_cap_then_terminal(self):
        d = self.recovery.handle(self._node(retry_count=0), TransientError("429"))
        self.assertIs(d.action, RecoveryAction.RETRY)
        self.assertGreater(d.delay_s, 0)
        d = self.recovery.handle(self._node(retry_count=2), TransientError("429"))
        self.assertIs(d.action, RecoveryAction.FAILED)
        self.assertIn("exhausted", d.reason)

    def test_logic_failure_is_terminal_in_phase1(self):
        d = self.recovery.handle(self._node(), LogicError("wrong approach"))
        self.assertIs(d.action, RecoveryAction.FAILED)
        self.assertIs(d.failure_class, FailureClass.LOGIC)


class SchedulerFailurePathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "agent.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, worker, max_retries=2):
        graph = TaskGraph(self.db)
        bus = EventBus(self.db)
        scheduler = Scheduler(
            graph, bus, worker,
            poll_interval_s=0.05,
            recovery=RecoveryManager(max_retries=max_retries, base_backoff_s=0.01),
        )
        asyncio.run(scheduler.run_until_idle(timeout_s=20))
        return graph, bus

    def test_transient_then_success_takes_retry_path(self):
        attempts = {"n": 0}

        async def flaky(task, ctx):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise TransientError("simulated 429")
            return WorkerResult(result_summary="ok after retry")

        graph, bus = self._run_with_task(flaky)
        task = graph.all_tasks()[0]
        self.assertEqual(task.status, "done")
        self.assertEqual(task.retry_count, 1)
        types = [e.type for e in bus.replay()]
        self.assertEqual(types.count("task.started"), 2)
        self.assertNotIn("task.failed", types)

    def test_persistent_failure_is_terminal_and_blocks_dependents(self):
        async def always_fails(task, ctx):
            raise TransientError("provider down")

        graph, bus = self._run_with_task(always_fails, child_goal="dependent")
        parent, child = graph.all_tasks()
        self.assertEqual(parent.status, "failed")
        self.assertEqual(parent.retry_count, 2)
        self.assertEqual(child.status, "blocked")
        failed = bus.replay(event_type="task.failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].payload["failure_class"], "transient")
        self.assertEqual(failed[0].payload["dependents_blocked"], [child.id])

    def test_unknown_exception_does_not_kill_scheduler(self):
        async def buggy(task, ctx):
            raise RuntimeError("bug in worker")

        graph, bus = self._run_with_task(buggy, max_retries=0)
        self.assertEqual(graph.all_tasks()[0].status, "failed")
        # Scheduler survived to tell the tale:
        self.assertEqual(len(bus.replay(event_type="task.failed")), 1)

    def _run_with_task(self, worker, max_retries=2, child_goal=None):
        graph = TaskGraph(self.db)
        parent = graph.add_task("root")
        if child_goal:
            graph.add_task(child_goal, depends_on=[parent.id])
        graph.close()
        return self._run(worker, max_retries=max_retries)


if __name__ == "__main__":
    unittest.main()
