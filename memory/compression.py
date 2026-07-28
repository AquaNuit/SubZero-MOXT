"""Compression (spec §5): subtask-close and mid-task compression.

Two moments:
1. **Subtask close** — the full transcript is compressed to *structured
   JSON* (`decisions[]`, `files_touched[]`, `open_questions[]`, `result`),
   the full transcript goes to an external log store keyed by task_id, and
   only the structured summary reaches the parent's context.
2. **Mid-task** — when the context-pressure monitor fires, everything but
   the last N turns is summarized so the active context stays bounded
   (Phase 2 acceptance test).

The compressor has two paths: an LLM path (a provider producing JSON) and a
deterministic heuristic path. The heuristic path keeps tests hermetic and
guarantees compression never fails the task — if the LLM path errors or
returns unparseable output, we fall back, never crash.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

log = logging.getLogger("memory.compression")


@dataclass
class CompressedSummary:
    """The structured shape downstream retrieval filters on."""
    result: str = ""
    decisions: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "CompressedSummary":
        data = json.loads(raw)
        return cls(
            result=str(data.get("result", "")),
            decisions=[str(d) for d in data.get("decisions", [])],
            files_touched=[str(f) for f in data.get("files_touched", [])],
            open_questions=[str(q) for q in data.get("open_questions", [])],
        )

    def as_result_summary(self, max_chars: int = 400) -> str:
        """The compact string a parent task sees in place of the transcript.

        The result portion is capped first so the structured fields
        (Decisions/Files/Open) always survive the final truncation — they
        are what downstream retrieval filters on.
        """
        parts = [self.result[:200]] if self.result else []
        if self.decisions:
            parts.append("Decisions: " + "; ".join(self.decisions[:5]))
        if self.files_touched:
            parts.append("Files: " + ", ".join(self.files_touched[:10]))
        if self.open_questions:
            parts.append("Open: " + "; ".join(self.open_questions[:5]))
        text = " | ".join(parts) or "(no summary)"
        return text[:max_chars]


class Summarizer(Protocol):
    """Anything that can turn messages into a CompressedSummary."""

    def summarize(self, messages: list[dict[str, str]]) -> CompressedSummary: ...


class HeuristicSummarizer:
    """Deterministic stdlib extractor — the hermetic default and fallback."""

    _DECISION_RE = re.compile(
        r"(?i)\b(?:decided|decision|chose|chosen|going with|will use)\b[:\s](.+)")
    _QUESTION_RE = re.compile(r"(?i)(?:^QUESTION:\s*(.+)|([^.!?\n]{10,}\?))")
    _FILE_RE = re.compile(
        r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|go|rs|c|h|cpp|java|"
        r"sh|md|json|ya?ml|toml|sql|html|css|txt|log))(?![\w/-])")
    # Note: sentence-final periods must not kill a match ("...edited db.py."),
    # so the trailing lookahead excludes word chars and '/' but not '.'.

    def summarize(self, messages: list[dict[str, str]]) -> CompressedSummary:
        decisions: list[str] = []
        questions: list[str] = []
        files: list[str] = []
        last_assistant = ""
        for msg in messages:
            content = msg.get("content", "")
            if msg.get("role") == "assistant" and content.strip():
                last_assistant = content.strip()
            for m in self._DECISION_RE.finditer(content):
                decisions.append(m.group(1).strip()[:200])
            for m in self._QUESTION_RE.finditer(content):
                q = (m.group(1) or m.group(2) or "").strip()
                if q:
                    questions.append(q[:200])
            files.extend(self._FILE_RE.findall(content))
        return CompressedSummary(
            result=last_assistant[:400],
            decisions=_dedup(decisions),
            files_touched=_dedup(files),
            open_questions=_dedup(questions),
        )


class LLMSummarizer:
    """Provider-backed summarizer with a hard fallback to the heuristic.

    `provider` is the Phase 1 Provider interface (complete(messages)). Any
    error — provider down, non-JSON output — falls back to heuristic so
    compression can never kill a running task.
    """

    PROMPT = (
        "Compress this work transcript into JSON with exactly these keys: "
        '{"result": str, "decisions": [str], "files_touched": [str], '
        '"open_questions": [str]}. Output only the JSON object.'
    )

    def __init__(self, provider, *, model: Optional[str] = None,
                 fallback: Optional[Summarizer] = None):
        self.provider = provider
        self.model = model
        self.fallback = fallback or HeuristicSummarizer()

    def summarize(self, messages: list[dict[str, str]]) -> CompressedSummary:
        import asyncio
        transcript = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages)
        try:
            completion = asyncio.run(self.provider.complete([
                {"role": "system", "content": self.PROMPT},
                {"role": "user", "content": transcript[-8000:]},
            ], model=self.model, temperature=0.0, max_tokens=800))
            match = re.search(r"\{.*\}", completion.content, re.S)
            if not match:
                raise ValueError("no JSON object in LLM output")
            return CompressedSummary.from_json(match.group(0))
        except Exception as exc:  # noqa: BLE001 — fallback by design
            log.warning("LLM summarize failed (%r); using heuristic fallback", exc)
            return self.fallback.summarize(messages)


class TranscriptStore:
    """External log store for full transcripts, keyed by task_id.

    Files live outside the DB (`state/transcripts/<task_id>.jsonl`); the
    task row's `full_log_ref` points here. Never loaded into context by
    default — only an explicit debugging step reads it.
    """

    def __init__(self, directory: str | Path = "state/transcripts"):
        self.dir = Path(directory)

    def save(self, task_id: str, messages: list[dict[str, str]]) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{task_id}.jsonl"
        with path.open("w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")
        return str(path)

    def load(self, ref: str) -> list[dict[str, str]]:
        path = Path(ref)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip()]


def mid_task_compress(
    messages: list[dict[str, str]],
    summarizer: Summarizer,
    *,
    keep_last_n: int = 6,
) -> tuple[list[dict[str, str]], Optional[CompressedSummary]]:
    """Summarize everything but the last N turns.

    Keeps the first message (the task goal/system framing) verbatim, swaps
    the middle for one system message holding the structured summary, and
    preserves the last `keep_last_n` turns untouched. Returns the new
    bounded list plus the summary (None if nothing needed compressing).
    """
    if len(messages) <= keep_last_n + 1:
        return messages, None
    head, middle, tail = (messages[0],
                          messages[1:-keep_last_n],
                          messages[-keep_last_n:])
    if not middle:
        return messages, None
    summary = summarizer.summarize(middle)
    compressed = [head, {
        "role": "system",
        "content": "[summary of earlier turns] " + summary.as_result_summary(),
    }, *tail]
    return compressed, summary


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
