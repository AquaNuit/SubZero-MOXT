"""Working memory, pressure monitor, and retriever tests (spec §5)."""

import tempfile
import unittest
from pathlib import Path

from memory.long_term import DecisionMemory, ProjectMemory
from memory.retrieval import Retriever
from memory.vector_store import HashEmbedder, VectorStore
from memory.working_memory import (
    ContextPressureMonitor,
    HeuristicTokenCounter,
    WorkingMemory,
)
from memory.workspace_indexer import WorkspaceIndexer
from memory.compression import HeuristicSummarizer


class WorkingMemoryAssemblyTest(unittest.TestCase):
    def test_assembly_order_and_minimality(self):
        wm = WorkingMemory()
        messages = wm.assemble(goal="fix the bug", parent_summary="parent did X")
        self.assertEqual(len(messages), 2)
        self.assertIn("fix the bug", messages[0]["content"])
        self.assertIn("parent did X", messages[1]["content"])

    def test_no_retriever_no_query_stays_minimal(self):
        wm = WorkingMemory()
        self.assertEqual(len(wm.assemble(goal="g")), 1)


class PressureMonitorTest(unittest.TestCase):
    def test_threshold_fires_at_70_percent(self):
        counter = HeuristicTokenCounter()
        monitor = ContextPressureMonitor(window_tokens=1000, counter=counter)
        small = [{"role": "user", "content": "x" * 400}]   # ~104 tokens
        big = [{"role": "user", "content": "x" * 4000}]    # ~1004 tokens
        self.assertFalse(monitor.check(small).should_compress)
        report = monitor.check(big)
        self.assertTrue(report.should_compress)
        self.assertGreaterEqual(report.ratio, 0.70)

    def test_compress_if_needed_bounds_usage(self):
        monitor = ContextPressureMonitor(
            window_tokens=400, threshold=0.7, keep_last_n=2)
        messages = [{"role": "system", "content": "GOAL"}]
        for i in range(30):
            messages.append({"role": "user", "content": f"turn {i} " + "y" * 40})
        compressed, summary, report = monitor.compress_if_needed(
            messages, HeuristicSummarizer())
        self.assertIsNotNone(summary)
        self.assertLess(report.ratio, monitor.threshold)
        self.assertLess(report.used_tokens,
                        monitor.check(messages).used_tokens)


class RetrieverIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = str(tmp / "agent.db")
        root = tmp / "ws"
        root.mkdir()
        (root / "db.py").write_text(
            "def connect_database(dsn):\n    \"\"\"Open the database connection.\"\"\"\n")
        (root / "ui.py").write_text("def render_button(label):\n    pass\n")
        self.vs = VectorStore(self.db)
        # 1024 dims: hash-embedder collisions are rare enough that keyword
        # overlap reliably ranks (256 in other tests is fine for 3+ shared
        # tokens; this test shares fewer).
        self.emb = HashEmbedder(dimension=1024)
        self.indexer = WorkspaceIndexer(root, self.db, self.vs, self.emb)
        self.indexer.scan()
        self.decisions = DecisionMemory(self.db, self.vs, self.emb)
        self.project = ProjectMemory(self.db)

    def tearDown(self):
        self.indexer.close()
        self.decisions.close()
        self.project.close()
        self.vs.close()
        self._tmp.cleanup()

    def test_gather_merges_all_three_sources(self):
        self.project.set_fact("language", "python", source="scan")
        # Query overlaps all three sources (hash embedder is keyword-based).
        self.decisions.record("chose sqlite for the database connection layer",
                              outcome="worked, kept zero services")
        retriever = Retriever(workspace_indexer=self.indexer,
                              decision_memory=self.decisions,
                              project_memory=self.project)
        chunks = retriever.gather("database connection", k=6)
        sources = {c.source for c in chunks}
        self.assertIn("project", sources)
        self.assertIn("decision", sources)
        self.assertIn("workspace", sources)
        # Capped and deduplicated.
        self.assertLessEqual(len(chunks), 6)
        self.assertEqual(len({c.ref for c in chunks}), len(chunks))

    def test_assemble_includes_retrieved_context(self):
        self.decisions.record("avoid global state in scheduler",
                              outcome="tests got simpler")
        retriever = Retriever(decision_memory=self.decisions)
        wm = WorkingMemory(retriever)
        messages = wm.assemble(goal="refactor scheduler",
                             retrieval_query="scheduler global state")
        self.assertEqual(len(messages), 2)
        self.assertIn("Retrieved context:", messages[1]["content"])
        self.assertIn("avoid global state", messages[1]["content"])

    def test_empty_sources_return_nothing(self):
        retriever = Retriever()
        self.assertEqual(retriever.gather("anything"), [])


if __name__ == "__main__":
    unittest.main()
