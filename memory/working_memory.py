"""Working memory + context-pressure detection (spec §5).

Working memory rule: only three things ride along in context by default —
the current subtask's goal, its immediate parent's `result_summary`, and
whatever retrieval pulls in. Nothing else.

Context pressure: token usage is tracked per running agent; at a
configurable threshold (default 70% of the model window) a mid-task
compression pass fires *before* a call fails over-length. Token counting is
behind an interface — the stdlib heuristic (chars/4) is the default; a real
tokenizer drops in later without callers changing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .compression import CompressedSummary, Summarizer, mid_task_compress


class TokenCounter(Protocol):
    def count_messages(self, messages: list[dict[str, str]]) -> int: ...


class HeuristicTokenCounter:
    """~4 chars/token + per-message overhead. Conservative enough for
    pressure detection; swap for a model tokenizer when one ships."""

    PER_MESSAGE_OVERHEAD = 4
    CHARS_PER_TOKEN = 4

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        total = 0
        for msg in messages:
            total += self.PER_MESSAGE_OVERHEAD
            total += math.ceil(len(msg.get("content", "")) / self.CHARS_PER_TOKEN)
        return total


@dataclass
class PressureReport:
    used_tokens: int
    window_tokens: int
    ratio: float
    should_compress: bool


class ContextPressureMonitor:
    """Fires at `threshold` of the model window (spec: start at ~70%)."""

    def __init__(
        self,
        window_tokens: int,
        *,
        threshold: float = 0.70,
        keep_last_n: int = 6,
        counter: Optional[TokenCounter] = None,
    ):
        if not 0 < threshold < 1:
            raise ValueError("threshold must be in (0, 1)")
        self.window_tokens = window_tokens
        self.threshold = threshold
        self.keep_last_n = keep_last_n
        self.counter = counter or HeuristicTokenCounter()

    def check(self, messages: list[dict[str, str]]) -> PressureReport:
        used = self.counter.count_messages(messages)
        ratio = used / self.window_tokens if self.window_tokens else 1.0
        return PressureReport(
            used_tokens=used,
            window_tokens=self.window_tokens,
            ratio=ratio,
            should_compress=ratio >= self.threshold,
        )

    def compress_if_needed(
        self,
        messages: list[dict[str, str]],
        summarizer: Summarizer,
    ) -> tuple[list[dict[str, str]], Optional[CompressedSummary], PressureReport]:
        """The bounded-context guarantee: after this call, either usage is
        below threshold or compression ran (and was recorded)."""
        report = self.check(messages)
        if not report.should_compress:
            return messages, None, report
        compressed, summary = mid_task_compress(
            messages, summarizer, keep_last_n=self.keep_last_n)
        return compressed, summary, self.check(compressed)


class WorkingMemory:
    """Assembles the minimal context for one task (spec §5 rule 1)."""

    def __init__(self, retriever=None, *, retrieval_k: int = 6):
        self.retriever = retriever
        self.retrieval_k = retrieval_k

    def assemble(
        self,
        *,
        goal: str,
        parent_summary: Optional[str] = None,
        retrieval_query: Optional[str] = None,
    ) -> list[dict[str, str]]:
        """[goal] + [parent summary] + [retrieved chunks] — nothing else."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"Current task: {goal}"}
        ]
        if parent_summary:
            messages.append({
                "role": "system",
                "content": f"Parent task result: {parent_summary}",
            })
        if self.retriever is not None and retrieval_query:
            chunks = self.retriever.gather(retrieval_query, k=self.retrieval_k)
            if chunks:
                body = "\n\n".join(
                    f"[{c.source}:{c.ref}] {c.text}" for c in chunks)
                messages.append({
                    "role": "system",
                    "content": f"Retrieved context:\n{body}",
                })
        return messages
