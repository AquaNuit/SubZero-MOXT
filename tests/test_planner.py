"""Planner parsing tests (Phase 4): defensive JSON extraction with retry."""

import asyncio
import unittest

from agent.planner import Planner
from kernel.recovery import LogicError
from providers.base import Completion


class ScriptedProvider:
    """Fake provider: plays back scripted responses, records every call."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        item = self.scripts.pop(0)
        content = item(messages) if callable(item) else item
        return Completion(content=content, model="scripted",
                          provider="scripted")

    async def health(self):
        return None


GOOD_JSON = (
    '{"analysis": "divide lacks a zero guard", "edits": [{"path": "ops.py",'
    ' "find": "def divide(a, b):\\n    return a / b",'
    ' "replace": "def divide(a, b):\\n    if b == 0:\\n        raise'
    ' ValueError()\\n    return a / b"}],'
    ' "test_command": "python3 -m unittest test_ops"}'
)


def run(coro):
    return asyncio.run(coro)


class PlannerParseTest(unittest.TestCase):
    def test_clean_json(self):
        planner = Planner(ScriptedProvider([GOOD_JSON]))
        plan = run(planner.plan([{"role": "user", "content": "fix divide"}]))
        self.assertEqual(plan.analysis, "divide lacks a zero guard")
        self.assertEqual(len(plan.edits), 1)
        self.assertEqual(plan.edits[0].path, "ops.py")
        self.assertIn("raise ValueError", plan.edits[0].replace)
        self.assertEqual(plan.test_command, "python3 -m unittest test_ops")

    def test_json_embedded_in_prose(self):
        planner = Planner(ScriptedProvider(
            ["Sure! Here is the plan:\n" + GOOD_JSON + "\nHope this helps"]))
        plan = run(planner.plan([{"role": "user", "content": "x"}]))
        self.assertEqual(len(plan.edits), 1)

    def test_retry_after_garbage_then_success(self):
        provider = ScriptedProvider(["let me think out loud...", GOOD_JSON])
        planner = Planner(provider)
        plan = run(planner.plan([{"role": "user", "content": "x"}]))
        self.assertEqual(len(plan.edits), 1)
        self.assertEqual(len(provider.calls), 2)
        # The retry carried the stricter prompt.
        self.assertIn("ONLY the JSON", provider.calls[1][-1]["content"])

    def test_persistent_garbage_raises_logic(self):
        planner = Planner(ScriptedProvider(["nope", "still nope"]))
        with self.assertRaises(LogicError):
            run(planner.plan([{"role": "user", "content": "x"}]))

    def test_missing_test_command_rejected(self):
        bad = '{"analysis": "x", "edits": [], "test_command": ""}'
        planner = Planner(ScriptedProvider([bad, bad]), max_attempts=2)
        with self.assertRaisesRegex(LogicError, "test_command"):
            run(planner.plan([{"role": "user", "content": "x"}]))

    def test_edit_missing_keys_rejected(self):
        bad = ('{"analysis": "x", "edits": [{"path": "a.py"}],'
               ' "test_command": "pytest"}')
        planner = Planner(ScriptedProvider([bad, bad]), max_attempts=2)
        with self.assertRaisesRegex(LogicError, "path/find/replace"):
            run(planner.plan([{"role": "user", "content": "x"}]))

    def test_replan_carries_failure_context(self):
        provider = ScriptedProvider([GOOD_JSON])
        planner = Planner(provider)
        run(planner.replan_with_failure(
            [{"role": "user", "content": "fix divide"}], "FAILED test_ops"))
        sent = provider.calls[0]
        self.assertTrue(any("FAILED test_ops" in m["content"]
                            for m in sent))
        self.assertTrue(any("genuinely different" in m["content"]
                            for m in sent))


if __name__ == "__main__":
    unittest.main()
