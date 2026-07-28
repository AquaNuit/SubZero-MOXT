"""Workspace indexer tests (spec §5.1).

Includes the Phase 2 acceptance property: touching one file updates ONLY
that file's subgraph (symbols, imports, embedding chunks) — everything else
is left byte-for-byte untouched (no re-embed, no re-parse).
"""

import tempfile
import time
import unittest
from pathlib import Path

from memory.vector_store import HashEmbedder, VectorStore
from memory.workspace_indexer import WorkspaceIndexer

FILE_A = '''"""Module A."""
import b
from c import helper_c


def main_a(x):
    return helper_b(x) + helper_c(x)
'''

FILE_B = '''"""Module B."""


def helper_b(x):
    """Database connection helper."""
    return x * 2


class Beta:
    def method_b(self):
        return helper_b(1)
'''

FILE_C = '''"""Module C."""


def helper_c(x):
    return x + 1
'''


class WorkspaceIndexerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.root = tmp / "ws"
        self.root.mkdir()
        (self.root / "a.py").write_text(FILE_A)
        (self.root / "b.py").write_text(FILE_B)
        (self.root / "c.py").write_text(FILE_C)
        (self.root / "notes.bin").write_bytes(b"\x00\x01")  # skipped ext
        self.db = str(tmp / "agent.db")
        self.vs = VectorStore(self.db)
        self.indexer = WorkspaceIndexer(self.root, self.db, self.vs,
                                        HashEmbedder(dimension=256))
        self.report1 = self.indexer.scan()

    def tearDown(self):
        self.indexer.close()
        self.vs.close()
        self._tmp.cleanup()

    def test_initial_scan_indexes_files_symbols_imports(self):
        self.assertEqual(sorted(self.report1.added), ["a.py", "b.py", "c.py"])
        self.assertEqual(self.report1.skipped, 1)  # notes.bin
        # Symbols via ast: functions, classes, methods with kinds.
        hits = self.indexer.where_is("helper_b")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].file_path, "b.py")
        self.assertEqual(hits[0].kind, "function")
        self.assertEqual(hits[0].line, 4)
        method = self.indexer.where_is("method_b")
        self.assertEqual(method[0].kind, "method")
        self.assertEqual(self.indexer.where_is("Beta")[0].kind, "class")
        # Imports recorded per file.
        self.assertEqual(self.indexer.imports_of("a.py"), ["b", "c"])

    def test_dependents_graph_query(self):
        self.assertEqual(self.indexer.dependents_of("b.py"), ["a.py"])
        self.assertEqual(self.indexer.dependents_of("c.py"), ["a.py"])
        self.assertEqual(self.indexer.dependents_of("a.py"), [])

    def test_search_code_finds_relevant_chunk(self):
        hits = self.indexer.search_code("database connection helper", k=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].metadata["path"], "b.py")

    def test_rescan_without_changes_touches_nothing(self):
        report = self.indexer.scan()
        self.assertEqual(report.added, [])
        self.assertEqual(report.changed, [])
        self.assertEqual(report.removed, [])
        self.assertEqual(report.unchanged, 3)
        self.assertEqual(report.chunks_embedded, 0)
        self.assertEqual(report.symbols_written, 0)

    def test_acceptance_only_changed_files_subgraph_updates(self):
        """Phase 2 acceptance: modify ONE file -> only its subgraph updates."""
        b_before = self.indexer.file_record("b.py")
        c_before = self.indexer.file_record("c.py")
        refs_before = self.vs.refs("workspace")
        time.sleep(0.02)  # ensure a changed embedded_at would be visible

        (self.root / "a.py").write_text(FILE_A + "\n\ndef extra_a():\n    pass\n")
        report = self.indexer.scan()

        # Only a.py was re-indexed...
        self.assertEqual(report.changed, ["a.py"])
        self.assertEqual(report.added, [])
        self.assertEqual(report.unchanged, 2)
        a_chunks = len([r for r in self.vs.refs("workspace")
                        if ":a.py#" in r])
        self.assertGreaterEqual(a_chunks, 1)
        self.assertEqual(report.chunks_embedded, a_chunks,
                         "no other file's chunks were re-embedded")
        # ...its new symbol is visible...
        self.assertEqual(self.indexer.where_is("extra_a")[0].file_path, "a.py")
        # ...and b/c rows + chunk refs are byte-for-byte untouched.
        self.assertEqual(self.indexer.file_record("b.py")["embedded_at"],
                         b_before["embedded_at"])
        self.assertEqual(self.indexer.file_record("c.py")["embedded_at"],
                         c_before["embedded_at"])
        b_refs = [r for r in refs_before if ":b.py#" in r or ":c.py#" in r]
        for ref in b_refs:
            self.assertIn(ref, self.vs.refs("workspace"))

    def test_removed_file_leaves_no_trace(self):
        (self.root / "c.py").unlink()
        report = self.indexer.scan()
        self.assertEqual(report.removed, ["c.py"])
        self.assertEqual(self.indexer.where_is("helper_c"), [])
        self.assertNotIn("c.py", self.indexer.indexed_files())
        self.assertFalse(any(":c.py#" in r for r in self.vs.refs("workspace")))

    def test_non_python_symbols_via_regex(self):
        (self.root / "app.js").write_text(
            "import { x } from './mod';\n"
            "export function renderApp() {}\n"
            "class Widget {}\n")
        self.indexer.scan()
        self.assertEqual(self.indexer.where_is("renderApp")[0].file_path, "app.js")
        self.assertEqual(self.indexer.where_is("Widget")[0].kind, "class")
        self.assertEqual(self.indexer.imports_of("app.js"), ["./mod"])


if __name__ == "__main__":
    unittest.main()
