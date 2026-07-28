# Implementation Status

Last updated: 2026-07-28 (Phase 4 complete)

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Kernel (task graph, scheduler, event bus, recovery, hard gate) + provider interface (local Ollama) | **DONE — 35/35 tests pass** |
| 2 | Memory/context system + workspace indexer | **DONE — 33/33 tests pass (68 total)** |
| 3 | Linux tools + execution engine | **DONE — 53/53 tests pass (121 total)** |
| 4 | Coding agent workflow (plan/edit/test/debug loop) | **DONE — 11/11 tests pass (132 total)** |
| 4.5 | Notifier/Command layer, NIM key pool, remaining providers, recovery classification upgrade | Not started |
| 5 | Browser automation | Not started |
| 5.5 | Static RE tooling (Ghidra bridge) | Not started |
| 6 | Multi-agent orchestration | Not started |
| 6.5 | Plugin SDK | Not started |
| 7 | Performance polish | Not started |

## Phase 1 — what exists and is verified

### kernel/task_graph.py
- SQLite `tasks` table exactly per spec §2.1 (id, parent_id, goal, status,
  assigned_agent, depends_on, artifacts, result_summary, full_log_ref,
  timestamps, retry_count).
- Status machine: `pending | ready | running | blocked | needs_human | done | failed`,
  enforced by a `CHECK` constraint.
- Dependency promotion (`refresh_ready`), oldest-first dispatch
  (`pop_ready`), crash recovery (`recover_interrupted`), recursive
  blocked-dependents cascade, persistence across connections.

### kernel/event_bus.py
- Durable outbox in the same SQLite DB — events survive restarts by
  construction (spec §2.2's "falls out for free" option, no Redis).
- At-least-once: per-consumer offsets advance only after handler success.
- Fan-out: every consumer sees every event (own offset).
- Dead-letter: poison events land in `dead_letters` after N attempts
  (default 5) with the error recorded; consumer moves past them.
- `replay(since=..., event_type=...)` for "what actually happened".
- `schema_version` on every event; current versions in `EVENT_SCHEMA`.

### kernel/scheduler.py
- asyncio loop: `run_forever` (daemon) and `run_until_idle` (tests/demos).
- `max_concurrency` (default 4), per-task timeout, exception boundary on
  every dispatch — worker crashes can never kill the loop (tested).
- Spawns child tasks from `WorkerResult.subtasks`; emits
  `task.started/progress/done/failed`.
- Hands failures to Recovery; applies RETRY (backoff, re-queue) or terminal
  FAILED (+ blocked cascade, event).

### kernel/recovery.py
- `FailureClass` + marker exceptions as the kernel's public classification
  contract (services raise these; the kernel never imports service code).
- Phase 1 policy: transient → capped retry, exponential backoff (base 1s,
  cap 60s) → terminal failed. Logic/environment → terminal failed with the
  reason named.
- **Deferred to Phase 4.5 by design:** logic → replan (needs the planner),
  environment/exhausted → `needs_human` escalation, per-tool/provider
  circuit breakers.

### kernel/hard_gate_enforcer.py (spec §1)
- `enforce()` returns instantly for `hard_gate: false`; for `true` it sets
  the task `needs_human`, publishes `task.needs_human` with the exact
  action + target, and blocks until a recorded decision.
- Approvals are keyed by `tool|action|target` — approving one target never
  approves another; decisions survive restarts.
- No bypass exists anywhere in the module. Vague calls (empty
  action/target) are rejected at the boundary.

### providers/
- `Provider` ABC: `complete(messages, **kw) -> Completion`, `health()`.
- Error taxonomy mapped to kernel markers (429→`RateLimited`,
  401/403→`ProviderAuthError` (environment), conn/5xx→`ProviderUnavailable`
  (transient)).
- `LocalOllamaProvider` — stdlib only, injectable transport (no live daemon
  needed in tests), default `qwen2.5-coder:7b` (~4.7GB q4, fits 6GB VRAM).
- Registry: `get_provider("local_ollama")`. NIM/OpenRouter/OpenCode Zen
  arrive Phase 4.5; adding one = new file + one registry line.

### config/routing.yaml
- Full routing table written per spec §4 (classes, verify, quorum,
  fallback_order, NIM pool 40rpm policy, OpenRouter free-tier reservation).
- Consumed by the router in Phase 4.5 — nothing reads it yet.

## Phase 2 — what exists and is verified

### memory/vector_store.py
- Namespaced embeddings as float32 blobs in the kernel SQLite DB; cosine
  top-k in Python (no vector-DB server). `Embedder` protocol +
  `HashEmbedder` (deterministic stdlib, 2-probe signed hashing) so all
  tests are hermetic; a real embedding model drops in behind the protocol.

### memory/workspace_indexer.py
- File index (path/language/sha256/embedded-at) — re-embeds only on hash
  change. Symbol graph: Python via `ast` (functions/classes/methods with
  line + signature), 8 other languages via conservative regex. Dependency
  graph (`workspace_imports`) with `dependents_of` reverse query.
- `scan()` is incremental: only added/changed/removed files' subgraphs are
  rewritten (acceptance-tested). Queries: `where_is`, `imports_of`,
  `dependents_of`, `search_code`.

### memory/long_term.py
- `DecisionMemory` (rows + embedded for retrieval; "tried X, outcome Y")
  and `ProjectMemory` (key/value facts with provenance).

### memory/compression.py
- `CompressedSummary` structured shape (result/decisions[]/files_touched[]/
  open_questions[]), `HeuristicSummarizer` + `LLMSummarizer` (hard fallback
  so compression never kills a task), `TranscriptStore` (full transcripts
  externalized, `full_log_ref` wired), `mid_task_compress`.

### memory/working_memory.py + memory/retrieval.py
- Minimal assembly rule enforced in code (goal + parent summary + retrieval
  only). `ContextPressureMonitor` fires mid-task compression at 70% of the
  window. `Retriever.gather` merges project/decision/workspace sources,
  deduped, capped.

## Phase 3 — what exists and is verified

### tools/base.py + tools/registry.py
- `Tool` ABC (name/description/typed `ParamSpec` params/**explicit**
  `hard_gate` bool), `ToolResult` with recovery `failure_class`,
  `ToolExecutor`: the single call path — validate → gate (hard_gate:true
  only, via the kernel enforcer; no enforcer = refused) → run → structured
  result. Nothing raises across the boundary. Runner protocol
  (`async (command, cwd, timeout_s) -> Completed`) + default
  `async_subprocess_runner` with process-group kill on timeout.
- Registry refuses malformed tools at load (missing gate flag/name/desc,
  duplicates) and renders `catalog()` for the planner.

### The six Phase 3 tools (all `hard_gate: false`)
- `filesystem` (read/write/append/list/exists/mkdir), `shell` (commands
  with cwd/timeout), `git` (init/status/log/diff/add/commit/branch),
  `python_exec` (code or script, subprocess-isolated),
  `package_manager` (distro-adaptive apt/dnf/pacman/flatpak/snap,
  dry-run support), `docker` (ps/images/pull/run/stop/logs/remove,
  daemon-down → environment failure).

## Phase 4 — what exists and is verified

### agent/planner.py
- `Planner` over the Phase 1 Provider interface: strict-JSON system
  prompt, defensive parse (JSON-in-prose extraction, per-edit validation,
  test_command required), one stricter retry, then `LogicError`.
- `replan_with_failure` feeds the failure output back and demands a
  genuinely different fix (spec §2.3 logic-failure semantics).

### agent/coding_worker.py
- `CodingWorker` — the Scheduler `worker` for coding tasks:
  indexer-first (`scan` + `where_is` on goal identifiers BEFORE any
  provider call; locations injected into the prompt) → WorkingMemory
  assembly → plan → bounded edit/test/debug loop (exact find/replace via
  ToolExecutor filesystem; shell test runs; failure-context replans;
  `LogicError` on exhaustion) → spec §5 close-out (re-index, structured
  compression, externalized transcript, decision recorded).
- Every tool call goes through the ToolExecutor; optional `trace` hook
  makes ordering/no-repo-scan properties testable.

## Test status

`python3 -m unittest discover -s tests -t . -v` → **132 tests, OK**
(also clean under `-W error::ResourceWarning`).

Suite map: `tests/` — task_graph (9), event_bus (8), hard_gate (6),
recovery (6), provider (5), scheduler_resume acceptance (1),
vector_store (6), workspace_indexer (6), compression (8), working_memory (5),
long_task bounded-context acceptance (1), tools_registry (16),
tools_exec (15), package_managers (14), docker (6),
phase3_acceptance (1), planner (7), coding_agent acceptance (4).

## Acceptance criteria (spec §10, Phase 1)

| Criterion | Where proven |
|-----------|--------------|
| **P1**: Kill/restart mid-task → resume, no re-prompt | `tests/test_scheduler_resume.py` (real `kill -9` on subprocess) |
| **P1**: Event survives a bus restart | `tests/test_event_bus.py::test_event_survives_bus_restart_unacked` |
| **P2**: Long task forces compression; active context stays bounded | `tests/test_memory_integration.py` (40-turn task, 400-token window, 3+ compressions, all post-compression ratios < threshold) |
| **P2**: Indexer updates only the changed file's subgraph | `tests/test_workspace_indexer.py::test_acceptance_only_changed_files_subgraph_updates` |
| **P3**: Install package, run script, report result, 2+ distros | `tests/test_phase3_acceptance.py` (apt + dnf code paths with verbatim command assertions, real script execution + git commit, all through the Scheduler) |
| **P4**: Fix a seeded bug end-to-end unattended, indexer-first | `tests/test_coding_agent.py` (real failing repo fixed via scheduler; `indexer.scan`/`where_is` before first provider call; `ops.py:8` location in first prompt; ONLY the edited file read — no repo scan; fix verified by real test rerun outside the agent) |

All pass. Phase 4.5 is unblocked.
