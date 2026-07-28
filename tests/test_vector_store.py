"""Vector store + hash embedder tests (spec §5 storage)."""

import math
import tempfile
import unittest
from pathlib import Path

from memory.vector_store import HashEmbedder, VectorStore


class HashEmbedderTest(unittest.TestCase):
    def test_dimension_and_determinism(self):
        emb = HashEmbedder(dimension=128)
        self.assertEqual(emb.dimension, 128)
        a = emb.embed(["hello world"])[0]
        b = emb.embed(["hello world"])[0]
        self.assertEqual(len(a), 128)
        self.assertEqual(a, b)  # deterministic

    def test_normalized_and_similarity_orders(self):
        emb = HashEmbedder(dimension=512)
        v = emb.embed(["database connection pooling"])[0]
        norm = math.sqrt(sum(x * x for x in v))
        self.assertAlmostEqual(norm, 1.0, places=5)
        related = emb.embed(["database connection timeout"])[0]
        unrelated = emb.embed(["sourdough bread baking"])[0]
        dot = lambda x, y: sum(a * b for a, b in zip(x, y))
        self.assertGreater(dot(v, related), dot(v, unrelated))

    def test_empty_text_zero_vector(self):
        emb = HashEmbedder()
        self.assertEqual(emb.embed([""])[0], [0.0] * emb.dimension)


class VectorStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "agent.db")
        self.vs = VectorStore(self.db)
        self.emb = HashEmbedder(dimension=256)

    def tearDown(self):
        self.vs.close()
        self._tmp.cleanup()

    def _put(self, namespace, ref, text):
        self.vs.upsert(namespace, ref, self.emb.embed([text])[0],
                       metadata={"text": text})

    def test_upsert_and_search_ranking(self):
        self._put("workspace", "a#chunk0", "python database connection pooling")
        self._put("workspace", "b#chunk0", "python database driver setup")
        self._put("workspace", "c#chunk0", "bread baking temperature guide")
        hits = self.vs.search(
            "workspace", self.emb.embed(["database connection"])[0], k=3)
        self.assertEqual(hits[0].ref, "a#chunk0")
        self.assertEqual(hits[-1].ref, "c#chunk0")
        self.assertGreater(hits[0].score, hits[-1].score)

    def test_namespaces_are_isolated(self):
        self._put("workspace", "x#0", "shared vocabulary tokens here")
        self._put("decisions", "decision:1", "shared vocabulary tokens here")
        hits = self.vs.search("workspace", self.emb.embed(["shared"])[0], k=10)
        self.assertEqual([h.ref for h in hits], ["x#0"])
        self.assertEqual(self.vs.count("workspace"), 1)
        self.assertEqual(self.vs.count("decisions"), 1)
        self.assertEqual(self.vs.count(), 2)

    def test_upsert_overwrites_same_ref(self):
        self._put("workspace", "a#0", "version one text")
        self._put("workspace", "a#0", "version two text")
        self.assertEqual(self.vs.count("workspace"), 1)

    def test_delete_and_prefix_delete(self):
        self._put("workspace", "f.py#chunk0", "alpha")
        self._put("workspace", "f.py#chunk1", "beta")
        self._put("workspace", "g.py#chunk0", "gamma")
        removed = self.vs.delete_where_ref_prefix("workspace", "f.py#")
        self.assertEqual(removed, 2)
        self.assertEqual(self.vs.refs("workspace"), ["g.py#chunk0"])
        self.vs.delete("workspace", "g.py#chunk0")
        self.assertEqual(self.vs.count("workspace"), 0)

    def test_persistence_across_instances(self):
        self._put("workspace", "a#0", "survives reopen")
        self.vs.close()
        vs2 = VectorStore(self.db)
        self.assertEqual(vs2.count("workspace"), 1)
        vs2.close()


if __name__ == "__main__":
    unittest.main()
