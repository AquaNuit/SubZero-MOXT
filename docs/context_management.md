# Context Management

**Status: Planned (Phase 2).** This is the design the Phase 2 build must
implement; nothing here exists yet except the task-graph fields that
support it (`result_summary`, `full_log_ref`).

## The problem

A long-running agent that stuffs every transcript into the next prompt will
blow the context window (and the latency budget) within a few subtasks.
Context is a scarce resource; the system manages it explicitly instead of
hoping the model copes.

## Rules

### 1. Working memory is minimal by default

Only three things ride along in an LLM call's context:

1. the current subtask's goal,
2. its immediate parent's `result_summary`,
3. whatever the retrieval step pulls in (see `memory_system.md`).

Nothing else. No full sibling transcripts, no whole-repo dumps, no "just in
case" history.

### 2. Compression on subtask close

When a subtask completes, its full transcript is compressed to **structured
JSON, not prose**:

```json
{
  "result": "...",
  "decisions": ["chose apt over snap because target is headless"],
  "files_touched": ["src/main.py"],
  "open_questions": ["does the CI box have docker?"]
}
```

- The **full transcript** goes to an external log store keyed by `task_id`;
  the task row's `full_log_ref` points at it. It is never loaded into
  context by default — only an explicit debugging/retrieval step reads it.
- The **structured summary** is stored as the task's `result_summary` input
  to the parent's context. Downstream retrieval filters on fields
  (e.g. "prior tasks that touched `src/main.py`"), which prose summaries
  can't support.

### 3. Context-pressure detection

Track the active token count per running agent. At a configurable threshold
(start at **70%** of the model's window), trigger a **mid-task compression
pass**: summarize everything except the last N turns, keep the current goal
verbatim, continue. Do this *before* a call fails with an over-length error
— waiting for the API to reject the request wastes a full round-trip and
risks losing the thread of the task.

### 4. Retrieval before generation

Before each LLM call, pull top-k relevant chunks (see `memory_system.md`).
The workspace indexer answers "where is X defined" deterministically, so
that question never spends tokens at all.

## Phase 2 acceptance test (spec §10)

Run a task long enough to force a compression pass; confirm the active
context stays bounded (token count never crosses the threshold without a
compression trigger firing) and the compressed output is the structured
shape above.
