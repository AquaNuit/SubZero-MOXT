"""Compression tests (spec §5): structured summaries, transcript store,
mid-task compression, LLM path with fallback."""

import json
import tempfile
import unittest
from pathlib import Path

from memory.compression import (
    CompressedSummary,
    HeuristicSummarizer,
    LLMSummarizer,
    TranscriptStore,
    mid_task_compress,
)
from providers.base import Completion

TRANSCRIPT = [
    {"role": "system", "content": "Current task: set up database"},
    {"role": "user", "content": "Should we use sqlite or postgres?"},
    {"role": "assistant",
     "content": "DECISION: chose sqlite because zero services on 16GB RAM. "
                "Edited memory/vector_store.py and config/routing.yaml."},
    {"role": "user", "content": "What about migrations?"},
    {"role": "assistant",
     "content": "OPEN QUESTION: who owns schema versioning? "
                "Touched kernel/db.py. Result: schema created."},
]


class HeuristicSummarizerTest(unittest.TestCase):
    def test_extracts_structured_fields(self):
        summary = HeuristicSummarizer().summarize(TRANSCRIPT)
        self.assertTrue(any("sqlite" in d for d in summary.decisions))
        self.assertIn("memory/vector_store.py", summary.files_touched)
        self.assertIn("kernel/db.py", summary.files_touched)
        self.assertTrue(any("schema versioning" in q
                            for q in summary.open_questions))
        self.assertEqual(summary.result, TRANSCRIPT[-1]["content"][:400])

    def test_empty_transcript(self):
        summary = HeuristicSummarizer().summarize([])
        self.assertEqual(summary.result, "")
        self.assertEqual(summary.decisions, [])


class CompressedSummaryShapeTest(unittest.TestCase):
    def test_json_roundtrip(self):
        original = CompressedSummary(
            result="done", decisions=["d1"], files_touched=["a.py"],
            open_questions=["q1"])
        loaded = CompressedSummary.from_json(original.to_json())
        self.assertEqual(loaded, original)

    def test_as_result_summary_is_compact(self):
        summary = CompressedSummary(
            result="r" * 1000, decisions=["d"], files_touched=["f.py"])
        text = summary.as_result_summary(max_chars=120)
        self.assertLessEqual(len(text), 120)
        # Structured, not prose-only: fields survive into the parent string.
        self.assertIn("Files:", summary.as_result_summary())


class TranscriptStoreTest(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TranscriptStore(tmp)
            ref = store.save("task-123", TRANSCRIPT)
            self.assertTrue(ref.endswith("task-123.jsonl"))
            self.assertEqual(store.load(ref), TRANSCRIPT)
            self.assertEqual(store.load(str(Path(tmp) / "nope.jsonl")), [])


class MidTaskCompressTest(unittest.TestCase):
    def test_keeps_goal_and_last_n_turns(self):
        messages = [{"role": "system", "content": "GOAL"}]
        for i in range(20):
            messages.append({"role": "user", "content": f"turn {i}"})
        compressed, summary = mid_task_compress(
            messages, HeuristicSummarizer(), keep_last_n=4)
        self.assertIsNotNone(summary)
        self.assertEqual(compressed[0]["content"], "GOAL")
        self.assertIn("[summary of earlier turns]", compressed[1]["content"])
        self.assertEqual([m["content"] for m in compressed[2:]],
                         ["turn 16", "turn 17", "turn 18", "turn 19"])
        self.assertEqual(len(compressed), 6)  # 1 goal + 1 summary + 4 tail

    def test_noop_when_short(self):
        messages = [{"role": "system", "content": "GOAL"},
                    {"role": "user", "content": "hi"}]
        compressed, summary = mid_task_compress(
            messages, HeuristicSummarizer(), keep_last_n=6)
        self.assertIs(summary, None)
        self.assertEqual(compressed, messages)


class _FakeProvider:
    def __init__(self, content=None, exc=None):
        self._content = content
        self._exc = exc

    async def complete(self, messages, **kwargs):
        if self._exc:
            raise self._exc
        return Completion(content=self._content, model="fake", provider="fake")


class LLMSummarizerTest(unittest.TestCase):
    def test_llm_path_parses_json(self):
        provider = _FakeProvider(content=json.dumps({
            "result": "shipped", "decisions": ["use sqlite"],
            "files_touched": ["db.py"], "open_questions": []}))
        summary = LLMSummarizer(provider).summarize(TRANSCRIPT)
        self.assertEqual(summary.result, "shipped")
        self.assertEqual(summary.files_touched, ["db.py"])

    def test_llm_failure_falls_back_never_crashes(self):
        provider = _FakeProvider(exc=RuntimeError("provider down"))
        summary = LLMSummarizer(provider).summarize(TRANSCRIPT)
        # Heuristic fallback still produces the structured shape.
        self.assertIn("memory/vector_store.py", summary.files_touched)

    def test_llm_non_json_falls_back(self):
        provider = _FakeProvider(content="sure, here is a summary in prose")
        summary = LLMSummarizer(provider).summarize(TRANSCRIPT)
        self.assertIn("kernel/db.py", summary.files_touched)


if __name__ == "__main__":
    unittest.main()
