# Implementation Status

Last updated: 2026-07-28 (Phase 1 complete)

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Kernel (task graph, scheduler, event bus, recovery, hard gate) + provider interface (local Ollama) | **DONE — 35/35 tests pass** |
| 2 | Memory/context system + workspace indexer | Not started |
| 3 | Linux tools + execution engine | Not started |
| 4 | Coding agent workflow | Not started |
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

## Test status

`python3 -m unittest discover -s tests -t . -v` → **35 tests, OK**
(also clean under `-W error::ResourceWarning`).

Suite map: `tests/` — task_graph (9), event_bus (8), hard_gate (6),
recovery (6), provider (5), scheduler_resume acceptance (1).

## Acceptance criteria (spec §10, Phase 1)

| Criterion | Where proven |
|-----------|--------------|
| Kill/restart mid-task → resume, no re-prompt | `tests/test_scheduler_resume.py` (real `kill -9` on subprocess) |
| Event survives a bus restart | `tests/test_event_bus.py::test_event_survives_bus_restart_unacked` + the acceptance test's post-kill event assertions |

Both pass. Phase 2 is unblocked.
