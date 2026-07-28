"""Phase 2 acceptance test (spec §10):

    Run a task long enough to force a compression pass; confirm active
    context stays bounded.

Runs a real task through the Phase 1 Scheduler whose worker holds a long
multi-turn "conversation" in WorkingMemory. A ContextPressureMonitor with a
small window forces repeated mid-task compressions. Asserts:
- compression actually fired (multiple times),
- active context stayed bounded after every compression (below threshold),
- each compression produced the structured summary shape,
- and the subtask-close flow stores the full transcript externally with
  only the structured summary going to the parent (full_log_ref set).
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from kernel.event_bus import EventBus
from kernel.scheduler import Scheduler, WorkerResult
from kernel.task_graph import TaskGraph
from memory.compression import HeuristicSummarizer, TranscriptStore
from memory.working_memory import ContextPressureMonitor, WorkingMemory

WINDOW_TOKENS = 400
THRESHOLD = 0.70
TURNS = 40  # pairs of user/assistant messages


class BoundedContextAcceptanceTest(unittest.TestCase):
    def test_long_task_forces_compression_and_stays_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "agent.db")
            transcripts = Path(tmp) / "transcripts"
            graph = TaskGraph(db)
            bus = EventBus(db)
            root = graph.add_task("long multi-turn task")

            monitor = ContextPressureMonitor(
                window_tokens=WINDOW_TOKENS, threshold=THRESHOLD, keep_last_n=4)
            summarizer = HeuristicSummarizer()
            store = TranscriptStore(transcripts)
            wm = WorkingMemory()

            stats = {
                "compressions": 0,
                "ratios_after_compression": [],
                "summaries": [],
                "max_ratio_seen_idle": 0.0,
            }

            async def worker(task, ctx):
                messages = wm.assemble(goal=task.goal)
                for i in range(TURNS):
                    messages.append({
                        "role": "user",
                        "content": f"turn {i}: please continue the analysis "
                                   + "blah " * 8})
                    messages.append({
                        "role": "assistant",
                        "content": (f"turn {i} reply. "
                                    + ("DECISION: keep pushing forward. "
                                       if i % 10 == 0 else "")
                                    + "Edited memory/working_memory.py. "
                                    + "blah " * 8)})
                    stats["max_ratio_seen_idle"] = max(
                        stats["max_ratio_seen_idle"],
                        monitor.check(messages).ratio)
                    compressed, summary, report = monitor.compress_if_needed(
                        messages, summarizer)
                    if summary is not None:
                        stats["compressions"] += 1
                        stats["summaries"].append(summary)
                        stats["ratios_after_compression"].append(report.ratio)
                        ctx.emit_progress(
                            f"mid-task compression #{stats['compressions']} "
                            f"at turn {i}")
                    messages = compressed

                # Subtask close: full transcript out, structured summary in.
                close_summary = summarizer.summarize(messages)
                ref = store.save(task.id, messages)
                return WorkerResult(
                    result_summary=close_summary.as_result_summary(),
                    full_log_ref=ref)

            scheduler = Scheduler(graph, bus, worker, poll_interval_s=0.05)
            asyncio.run(scheduler.run_until_idle(timeout_s=30))

            # --- The acceptance properties --------------------------------
            self.assertGreaterEqual(stats["compressions"], 3,
                                    "40 turns in a 400-token window must "
                                    "force multiple compression passes")
            self.assertGreater(stats["max_ratio_seen_idle"], THRESHOLD,
                               "the run must actually have crossed the "
                               "threshold, else the test proves nothing")
            for ratio in stats["ratios_after_compression"]:
                self.assertLess(ratio, THRESHOLD,
                                "active context exceeded threshold AFTER a "
                                "compression pass — not bounded")
            for summary in stats["summaries"]:
                # Structured shape, not free prose.
                self.assertTrue(hasattr(summary, "decisions"))
                self.assertTrue(hasattr(summary, "files_touched"))
                self.assertTrue(hasattr(summary, "open_questions"))

            # --- Subtask close landed in the graph -------------------------
            task = graph.all_tasks()[0]
            self.assertEqual(task.status, "done")
            self.assertIsNotNone(task.full_log_ref)
            self.assertTrue(Path(task.full_log_ref).exists())
            self.assertIn("Files:", task.result_summary)
            # Progress events documented the compressions.
            progress = bus.replay(event_type="task.progress")
            self.assertGreaterEqual(
                len([e for e in progress
                     if "compression" in e.payload.get("message", "")]), 3)

            graph.close()
            bus.close()


if __name__ == "__main__":
    unittest.main()
