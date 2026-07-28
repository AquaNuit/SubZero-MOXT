"""Task graph unit tests (spec §2.1)."""

import tempfile
import unittest
from pathlib import Path

from kernel.task_graph import TaskGraph


class TaskGraphTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "agent.db")
        self.graph = TaskGraph(self.db)

    def tearDown(self):
        self.graph.close()
        self._tmp.cleanup()

    def test_add_task_without_deps_is_ready(self):
        node = self.graph.add_task("root goal")
        self.assertEqual(node.status, "ready")
        fetched = self.graph.get(node.id)
        self.assertEqual(fetched.goal, "root goal")
        self.assertEqual(fetched.status, "ready")

    def test_dependency_blocks_until_done_then_promotes(self):
        a = self.graph.add_task("step A")
        b = self.graph.add_task("step B", depends_on=[a.id])
        self.assertEqual(b.status, "pending")

        self.assertEqual(self.graph.refresh_ready(), [])
        self.graph.update_status(a.id, "done", result_summary="A finished")
        promoted = self.graph.refresh_ready()
        self.assertEqual([n.id for n in promoted], [b.id])
        self.assertEqual(self.graph.get(b.id).status, "ready")

    def test_pop_ready_marks_running_oldest_first(self):
        first = self.graph.add_task("first")
        self.graph.add_task("second")
        popped = self.graph.pop_ready()
        self.assertEqual(popped.id, first.id)
        self.assertEqual(self.graph.get(first.id).status, "running")

    def test_recover_interrupted_requeues_running(self):
        node = self.graph.add_task("long task")
        self.graph.pop_ready()  # -> running
        recovered = self.graph.recover_interrupted()
        self.assertEqual([n.id for n in recovered], [node.id])
        self.assertEqual(self.graph.get(node.id).status, "ready")

    def test_result_fields_roundtrip(self):
        node = self.graph.add_task("produce artifacts")
        self.graph.update_status(
            node.id, "done",
            result_summary="short summary for parent",
            artifacts=["/tmp/out.txt"],
            full_log_ref="logs/task-123.jsonl",
        )
        fetched = self.graph.get(node.id)
        self.assertEqual(fetched.result_summary, "short summary for parent")
        self.assertEqual(fetched.artifacts, ["/tmp/out.txt"])
        self.assertEqual(fetched.full_log_ref, "logs/task-123.jsonl")

    def test_blocked_dependents_cascade(self):
        a = self.graph.add_task("A")
        b = self.graph.add_task("B", depends_on=[a.id])
        c = self.graph.add_task("C", depends_on=[b.id])
        blocked = self.graph.mark_blocked_dependents(a.id)
        self.assertEqual(set(blocked), {b.id, c.id})
        self.assertEqual(self.graph.get(c.id).status, "blocked")

    def test_retry_counter(self):
        node = self.graph.add_task("flaky")
        self.assertEqual(self.graph.increment_retry(node.id), 1)
        self.assertEqual(self.graph.increment_retry(node.id), 2)

    def test_persistence_across_instances(self):
        node = self.graph.add_task("survives reopen")
        self.graph.close()
        graph2 = TaskGraph(self.db)
        self.assertEqual(graph2.get(node.id).goal, "survives reopen")
        graph2.close()


if __name__ == "__main__":
    unittest.main()
