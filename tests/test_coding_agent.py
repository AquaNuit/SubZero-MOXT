"""Phase 4 acceptance test (spec §10):

    Agent fixes a seeded bug in a small repo end-to-end unattended, using
    the Workspace Indexer instead of a fresh full-repo scan.

A real repo with a real failing test is created in a tmp dir; the
scheduler runs the CodingWorker with a scripted provider (hermetic — the
"model" is a playback, everything else is 100% real: indexer, filesystem
edits, shell test runs, transcripts, decision memory).

Trace assertions prove the Phase 4 property:
- indexer.scan + where_is happen BEFORE the first provider call;
- the first provider prompt contains the indexer-derived location (ops.py:N);
- filesystem reads touch ONLY the edited file — no full-tree scan.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agent.coding_worker import CodingWorker, CodingWorkerConfig
from kernel.event_bus import EventBus
from kernel.recovery import RecoveryManager
from kernel.scheduler import Scheduler
from kernel.task_graph import TaskGraph
from memory.long_term import DecisionMemory
from memory.vector_store import HashEmbedder, VectorStore
from memory.workspace_indexer import WorkspaceIndexer
from providers.base import Completion
from tools.base import ToolExecutor
from tools.filesystem import FilesystemTool
from tools.registry import ToolRegistry
from tools.shell import ShellTool

OPS_PY = '''"""Calculator operations."""


def add(a, b):
    return a + b


def divide(a, b):
    return a / b
'''

TEST_OPS = '''import unittest
from ops import add, divide


class OpsTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_divide(self):
        self.assertEqual(divide(10, 2), 5)

    def test_divide_by_zero(self):
        with self.assertRaises(ValueError):
            divide(1, 0)


if __name__ == "__main__":
    unittest.main()
'''

FIXED_DIVIDE = ('def divide(a, b):\n    if b == 0:\n'
                '        raise ValueError("division by zero")\n'
                '    return a / b')

GOOD_PLAN = json.dumps({
    "analysis": "divide() has no zero guard",
    "edits": [{"path": "ops.py",
               "find": "def divide(a, b):\n    return a / b",
               "replace": FIXED_DIVIDE}],
    "test_command": "python3 -m unittest test_ops",
})

BAD_PLAN = json.dumps({  # applies cleanly but does NOT fix the tests
    "analysis": "wrong guess",
    "edits": [{"path": "ops.py",
               "find": "def divide(a, b):\n    return a / b",
               "replace": "def divide(a, b):\n    return 0"}],
    "test_command": "python3 -m unittest test_ops",
})

RECOVERY_PLAN = json.dumps({  # finds the bad edit's result, fixes for real
    "analysis": "previous edit was wrong; add the guard properly",
    "edits": [{"path": "ops.py",
               "find": "def divide(a, b):\n    return 0",
               "replace": FIXED_DIVIDE}],
    "test_command": "python3 -m unittest test_ops",
})


class ScriptedProvider:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        item = self.scripts.pop(0)
        return Completion(content=item(messages) if callable(item) else item,
                          model="scripted", provider="scripted")

    async def health(self):
        return None


def _seed_repo(root: Path) -> None:
    (root / "ops.py").write_text(OPS_PY)
    (root / "test_ops.py").write_text(TEST_OPS)


class CodingAgentAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        _seed_repo(self.repo)
        self.db = str(tmp / "agent.db")
        self.transcripts = str(tmp / "transcripts")

        self.vs = VectorStore(self.db)
        self.indexer = WorkspaceIndexer(self.repo, self.db, self.vs,
                                        HashEmbedder(dimension=512))
        self.decisions = DecisionMemory(self.db, self.vs,
                                        HashEmbedder(dimension=512))
        registry = ToolRegistry()
        registry.register(FilesystemTool())
        registry.register(ShellTool())
        self.executor = ToolExecutor(registry)

    def tearDown(self):
        self.indexer.close()
        self.decisions.close()
        self.vs.close()
        self._tmp.cleanup()

    def _run_task(self, scripts, goal, max_iterations=5):
        provider = ScriptedProvider(scripts)
        trace: list = []
        worker = CodingWorker(
            provider, self.executor,
            workspace_root=str(self.repo),
            indexer=self.indexer,
            decision_memory=self.decisions,
            transcript_dir=self.transcripts,
            config=CodingWorkerConfig(max_iterations=max_iterations),
            trace=trace)
        graph = TaskGraph(self.db)
        bus = EventBus(self.db)
        graph.add_task(goal)
        scheduler = Scheduler(
            graph, bus, worker, poll_interval_s=0.05,
            recovery=RecoveryManager(max_retries=0, base_backoff_s=0.01))
        asyncio.run(scheduler.run_until_idle(timeout_s=60))
        return graph.all_tasks()[0], provider, trace, bus

    # ------------------------------------------------------ the acceptance

    def test_seeded_bug_fixed_end_to_end_indexer_first(self):
        task, provider, trace, bus = self._run_task(
            [GOOD_PLAN],
            "Fix the failing test: divide() should raise ValueError on "
            "division by zero")

        # End-to-end: task done, fix is REAL (rerun tests outside the agent).
        self.assertEqual(task.status, "done", task.result_summary)
        self.assertIn("raise ValueError",
                      (self.repo / "ops.py").read_text())
        verify = asyncio.run(ShellTool().run(
            "python3 -m unittest test_ops", cwd=str(self.repo)))
        self.assertTrue(verify.ok, verify.output)

        # Indexer-first: scan + where_is BEFORE the first provider call.
        events = [e for e, _ in trace]
        first_provider = events.index("provider.call")
        self.assertLess(events.index("indexer.scan"), first_provider)
        self.assertLess(events.index("indexer.where_is"), first_provider)

        # The model was TOLD where divide lives — no full-repo scan needed.
        first_prompt = provider.calls[0]
        self.assertTrue(any("ops.py:8" in m["content"]
                            for m in first_prompt),
                        "indexer location (ops.py:8) missing from prompt")
        reads = [d for e, d in trace
                 if e == "tool" and d.startswith("filesystem.read")]
        self.assertEqual(reads, ["filesystem.read ops.py"],
                         "agent read files beyond the indexed target — "
                         "that's a repo scan, not indexer-first navigation")

        # Close-out: transcript externalized, decision recorded, summary set.
        self.assertTrue(Path(task.full_log_ref).exists())
        self.assertIn("ops.py", task.artifacts)
        recorded = self.decisions.recent(1)
        self.assertEqual(len(recorded), 1)
        self.assertIn("tests pass", recorded[0].outcome)
        done_events = bus.replay(event_type="task.done")
        self.assertEqual(len(done_events), 1)
        bus.close()

    # ------------------------------------------------------- debug loop

    def test_debug_loop_recovers_from_bad_first_edit(self):
        task, provider, trace, bus = self._run_task(
            [BAD_PLAN, RECOVERY_PLAN],
            "Fix the failing test: divide() should raise ValueError")
        self.assertEqual(task.status, "done", task.result_summary)
        self.assertEqual(len(provider.calls), 2)
        test_runs = [d for e, d in trace if e == "tool" and "unittest" in d]
        self.assertEqual(len(test_runs), 2)  # failed once, passed once
        verify = asyncio.run(ShellTool().run(
            "python3 -m unittest test_ops", cwd=str(self.repo)))
        self.assertTrue(verify.ok, verify.output)
        bus.close()

    def test_max_iterations_exhausted_fails_as_logic(self):
        task, provider, trace, bus = self._run_task(
            [BAD_PLAN, BAD_PLAN.replace("return 0", "return -1")],
            "Fix divide()", max_iterations=2)
        self.assertEqual(task.status, "failed")
        failed = bus.replay(event_type="task.failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].payload["failure_class"], "logic")
        self.assertIn("exhausted", failed[0].payload["error"])
        bus.close()

    def test_unparseable_plan_twice_fails_as_logic(self):
        task, _, _, bus = self._run_task(
            ["garbage", "still garbage"], "Fix divide()")
        self.assertEqual(task.status, "failed")
        failed = bus.replay(event_type="task.failed")
        self.assertEqual(failed[0].payload["failure_class"], "logic")
        bus.close()


if __name__ == "__main__":
    unittest.main()
