"""Hard-gate enforcement tests (spec §1 — non-negotiable).

These prove:
1. A hard_gate:true call never executes before an explicit approval.
2. The task moves to needs_human and a task.needs_human event names the
   exact action and target.
3. Approval lets it proceed; denial raises and the action never runs.
4. hard_gate:false calls run immediately, no event.
5. A recorded approval survives a 'restart' (re-entering the gate applies
   the recorded decision instead of asking again).
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from kernel.event_bus import EventBus
from kernel.hard_gate_enforcer import HardGateDenied, HardGateEnforcer, ToolCallSpec
from kernel.task_graph import TaskGraph

GATED_CALL = ToolCallSpec(
    tool_name="exploit_runner",
    action="run exploit/multi/handler",
    target="10.0.0.5:4444",
    hard_gate=True,
)
UNGATED_CALL = ToolCallSpec(
    tool_name="ghidra_bridge",
    action="decompile function at 0x401000",
    target="/tmp/sample.bin",
    hard_gate=False,
)


class HardGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "agent.db")
        self.graph = TaskGraph(self.db)
        self.task = self.graph.add_task("security analysis")
        self.enforcer = HardGateEnforcer(self.db, poll_interval_s=0.05)

    def tearDown(self):
        self.graph.close()
        self._tmp.cleanup()

    def _run_gated_tool(self, executed: list, errors: list):
        """Simulates a worker hitting a gated tool mid-execution."""
        def _worker():
            try:
                self.enforcer.enforce(self.task.id, GATED_CALL)
                executed.append(time.time())  # side effect AFTER the gate
            except HardGateDenied as exc:
                errors.append(exc)
        t = threading.Thread(target=_worker)
        t.start()
        return t

    def test_gated_call_blocks_until_approval(self):
        executed, errors = [], []
        t = self._run_gated_tool(executed, errors)

        time.sleep(0.3)
        # 1. Not executed yet — the gate is holding.
        self.assertEqual(executed, [])
        # 2. Task is needs_human, event names the exact action + target.
        self.assertEqual(self.graph.get(self.task.id).status, "needs_human")
        bus = EventBus(self.db)
        events = bus.replay(event_type="task.needs_human")
        bus.close()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["action"], "run exploit/multi/handler")
        self.assertEqual(events[0].payload["target"], "10.0.0.5:4444")

        # 3. Approve -> the worker proceeds.
        self.enforcer.approve(self.task.id, GATED_CALL, approver="at@telegram")
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(executed), 1)
        self.assertEqual(errors, [])

    def test_gated_call_denied_never_executes(self):
        executed, errors = [], []
        t = self._run_gated_tool(executed, errors)
        time.sleep(0.3)
        self.enforcer.deny(self.task.id, GATED_CALL, approver="at@telegram",
                           reason="not on this host")
        t.join(timeout=5)
        self.assertEqual(executed, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("denied", str(errors[0]).lower())

    def test_ungated_call_runs_immediately(self):
        start = time.time()
        self.enforcer.enforce(self.task.id, UNGATED_CALL)
        self.assertLess(time.time() - start, 0.5)
        bus = EventBus(self.db)
        self.assertEqual(bus.replay(event_type="task.needs_human"), [])
        bus.close()

    def test_gated_call_requires_concrete_action_and_target(self):
        vague = ToolCallSpec(tool_name="exploit_runner", action="",
                             target="", hard_gate=True)
        with self.assertRaises(ValueError):
            self.enforcer.enforce(self.task.id, vague)

    def test_recorded_approval_survives_restart(self):
        # Approval recorded, process 'dies' before the worker re-enters.
        self.enforcer.approve(self.task.id, GATED_CALL, approver="at@telegram")
        enforcer2 = HardGateEnforcer(self.db, poll_interval_s=0.05)  # restart
        start = time.time()
        enforcer2.enforce(self.task.id, GATED_CALL)  # applies recorded decision
        self.assertLess(time.time() - start, 1.0)

    def test_approval_is_bound_to_exact_target(self):
        self.enforcer.approve(self.task.id, GATED_CALL, approver="at@telegram")
        other_target = ToolCallSpec(
            tool_name=GATED_CALL.tool_name,
            action=GATED_CALL.action,
            target="192.168.1.99:4444",  # different target
            hard_gate=True,
        )
        self.assertTrue(self.enforcer.is_pending(self.task.id, other_target))


if __name__ == "__main__":
    unittest.main()
