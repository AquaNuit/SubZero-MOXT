"""Tool registry + executor contract tests (spec §6 + §1).

Registration-time enforcement, parameter typing, and the single gated call
path: hard_gate:true tools go through the kernel enforcer or don't run.
"""

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kernel.hard_gate_enforcer import HardGateEnforcer, ToolCallSpec
from kernel.task_graph import TaskGraph
from tools.base import ParamSpec, Tool, ToolExecutor, ToolResult
from tools.registry import ToolRegistry, ToolRegistrationError


def run(coro):
    return asyncio.run(coro)


class DummyTool(Tool):
    name = "dummy"
    description = "A valid dummy tool."
    hard_gate = False
    params = [ParamSpec("x", int, "a number"),
              ParamSpec("flag", bool, "a flag", required=False, default=False)]

    async def run(self, x: int, flag: bool = False) -> ToolResult:
        return ToolResult(True, output=f"x={x} flag={flag}")


class GatedDummyTool(Tool):
    name = "gated_dummy"
    description = "A gated dummy tool."
    hard_gate = True
    params = [ParamSpec("target_host", str, "the host")]

    async def run(self, target_host: str) -> ToolResult:
        return ToolResult(True, output=f"fired against {target_host}")


class RegistrationTest(unittest.TestCase):
    def test_valid_tool_registers_and_catalogs(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        self.assertIn("dummy", reg)
        entry = reg.catalog()[0]
        self.assertEqual(entry["name"], "dummy")
        self.assertEqual(entry["hard_gate"], False)
        self.assertEqual(entry["params"][0]["type"], "int")
        self.assertTrue(entry["params"][0]["required"])
        self.assertEqual(entry["params"][1]["default"], False)

    def test_missing_hard_gate_refused(self):
        class NoGate(Tool):
            name = "nogate"
            description = "x"
            params = []

            async def run(self):
                return ToolResult(True)

        with self.assertRaisesRegex(ToolRegistrationError, "hard_gate"):
            ToolRegistry().register(NoGate())

    def test_missing_name_and_description_refused(self):
        class NoName(Tool):
            hard_gate = False
            params = []

            async def run(self):
                return ToolResult(True)

        with self.assertRaisesRegex(ToolRegistrationError, "name"):
            ToolRegistry().register(NoName())

        class NoDesc(DummyTool):
            name = "nodesc"
            description = ""

        with self.assertRaisesRegex(ToolRegistrationError, "description"):
            ToolRegistry().register(NoDesc())

    def test_duplicate_name_refused(self):
        reg = ToolRegistry()
        reg.register(DummyTool())
        with self.assertRaisesRegex(ToolRegistrationError, "duplicate"):
            reg.register(DummyTool())

    def test_duplicate_param_refused(self):
        class DupParam(DummyTool):
            name = "dupparam"
            params = [ParamSpec("x", int), ParamSpec("x", str)]

        with self.assertRaisesRegex(ToolRegistrationError, "duplicate param"):
            ToolRegistry().register(DupParam())


class ValidationTest(unittest.TestCase):
    def test_coercion_and_defaults(self):
        tool = DummyTool()
        cleaned = tool.validate({"x": "42"})
        self.assertEqual(cleaned, {"x": 42, "flag": False})

    def test_bool_is_not_an_int(self):
        with self.assertRaisesRegex(Exception, "expects int"):
            DummyTool().validate({"x": True})

    def test_unknown_and_missing_params(self):
        tool = DummyTool()
        with self.assertRaisesRegex(Exception, "unknown param"):
            tool.validate({"x": 1, "zzz": 2})
        with self.assertRaisesRegex(Exception, "missing required"):
            tool.validate({})


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(DummyTool())
        self.reg.register(GatedDummyTool())

    def test_unknown_tool_is_logic_failure_not_crash(self):
        result = run(ToolExecutor(self.reg).execute("nope"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "logic")

    def test_param_error_is_logic_failure(self):
        result = run(ToolExecutor(self.reg).execute("dummy", x="not-a-number"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "logic")

    def test_ungated_tool_executes(self):
        result = run(ToolExecutor(self.reg).execute("dummy", x=7, flag=True))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "x=7 flag=True")

    def test_tool_exception_becomes_structured_result(self):
        class Boom(DummyTool):
            name = "boom"

            async def run(self, x, flag=False):
                raise RuntimeError("surprise")

        self.reg.register(Boom())
        result = run(ToolExecutor(self.reg).execute("boom", x=1))
        self.assertFalse(result.ok)
        self.assertIn("surprise", result.error)
        self.assertEqual(result.failure_class, "transient")

    def test_gated_tool_without_enforcer_is_refused(self):
        result = run(ToolExecutor(self.reg).execute(
            "gated_dummy", target_host="10.0.0.5",
            gate_action="probe", gate_target="10.0.0.5"))
        self.assertFalse(result.ok)
        self.assertIn("spec §1", result.error)

    def test_gated_tool_flows_through_enforcer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "agent.db")
            graph = TaskGraph(db)
            task = graph.add_task("gated work")
            enforcer = HardGateEnforcer(db, poll_interval_s=0.05)
            executor = ToolExecutor(self.reg, enforcer)

            outcome = {}

            def call_tool():
                outcome["result"] = asyncio.run(executor.execute(
                    "gated_dummy", task_id=task.id,
                    gate_action="probe port 4444", gate_target="10.0.0.5",
                    target_host="10.0.0.5"))

            t = threading.Thread(target=call_tool)
            t.start()
            time.sleep(0.3)
            self.assertNotIn("result", outcome)  # blocked at the gate
            self.assertEqual(graph.get(task.id).status, "needs_human")

            enforcer.approve(task.id, ToolCallSpec(
                tool_name="gated_dummy", action="probe port 4444",
                target="10.0.0.5", hard_gate=True), approver="at@telegram")
            t.join(timeout=5)
            self.assertTrue(outcome["result"].ok)
            self.assertIn("fired against 10.0.0.5", outcome["result"].output)
            graph.close()

    def test_gated_tool_denial_returns_structured_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "agent.db")
            graph = TaskGraph(db)
            task = graph.add_task("gated work")
            enforcer = HardGateEnforcer(db, poll_interval_s=0.05)
            executor = ToolExecutor(self.reg, enforcer)
            call = ToolCallSpec(tool_name="gated_dummy", action="probe",
                                target="10.0.0.5", hard_gate=True)
            enforcer.deny(task.id, call, approver="at@telegram")
            result = run(executor.execute(
                "gated_dummy", task_id=task.id, gate_action="probe",
                gate_target="10.0.0.5", target_host="10.0.0.5"))
            self.assertFalse(result.ok)
            self.assertEqual(result.data.get("gate"), "denied")
            graph.close()

    def test_gated_tool_requires_concrete_action_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "agent.db")
            enforcer = HardGateEnforcer(db, poll_interval_s=0.05)
            executor = ToolExecutor(self.reg, enforcer)
            result = run(executor.execute(
                "gated_dummy", task_id="t", gate_action="", gate_target="",
                target_host="10.0.0.5"))
            self.assertFalse(result.ok)
            self.assertEqual(result.failure_class, "logic")


if __name__ == "__main__":
    unittest.main()
