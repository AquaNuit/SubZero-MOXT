# Context Management

**Status: BUILT (Phase 2, 2026-07-28).** Context is a scarce resource; the
system manages it explicitly instead of hoping the model copes.

## The rules, as implemented

### 1. Working memory is minimal by default

`memory/working_memory.py::WorkingMemory.assemble` builds exactly:

1. the current subtask's goal (system message),
2. its immediate parent's `result_summary` (when present),
3. retrieved chunks from `Retriever.gather` (when a query is given).

Nothing else rides along. No sibling transcripts, no repo dumps.

### 2. Compression on subtask close

`memory/compression.py`:

- `CompressedSummary` — the structured shape: `result`, `decisions[]`,
  `files_touched[]`, `open_questions[]` (JSON, not prose — downstream
  retrieval filters on fields). `as_result_summary()` caps the result
  portion first so the structured fields always survive truncation.
- `TranscriptStore` — full transcripts go to
  `state/transcripts/<task_id>.jsonl`; the returned ref goes into the task
  row's `full_log_ref`. Never loaded into context by default.
- Two summarizers behind one protocol: `HeuristicSummarizer`
  (deterministic, stdlib, the default) and `LLMSummarizer` (provider-backed
  JSON extraction with a **hard fallback to heuristic on any failure** —
  compression must never kill a running task).

### 3. Context-pressure detection (built, acceptance-tested)

`ContextPressureMonitor(window_tokens, threshold=0.70, keep_last_n)`:

- `check(messages) -> PressureReport(used, window, ratio, should_compress)`
- `compress_if_needed(messages, summarizer)` — when usage ≥ threshold,
  `mid_task_compress` summarizes everything but the last N turns
  (goal message kept verbatim, middle swapped for one summary system
  message), returning the bounded list + the structured summary.

Token counting is behind `TokenCounter` (default `HeuristicTokenCounter`:
chars/4 + per-message overhead) — a model tokenizer drops in later.

This fires **before** a provider rejects an over-length request: no wasted
round-trip, no lost thread.

### 4. Retrieval before generation

`Retriever.gather(query, k)` runs before each LLM call; the indexer's
`where_is`/`dependents_of` answer structural questions with zero tokens.

## Known limits (tracked in known_issues.md)

- `HeuristicTokenCounter` is an estimate; compression may fire early/late
  vs. a real tokenizer. Acceptable: the failure mode it prevents (hard
  over-length rejection) is worse than a premature summary.
- `LLMSummarizer` is synchronous (`asyncio.run` inside); called from an
  async worker it falls back to heuristic. An async summarize path is a
  Phase 4 item when the real planner worker lands.

## Acceptance test (spec §10, Phase 2) — passes

`tests/test_memory_integration.py` runs a 40-turn task through the Phase 1
scheduler in a 400-token window: 3+ compression passes fired, post-
compression usage stayed below threshold every time, summaries structured,
transcript externalized with `full_log_ref` set and `Files:` in the
parent-visible `result_summary`.
