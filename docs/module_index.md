# Module Index

Everything that exists in the repo, file by file. Status legend:
**built** (implemented + tested) / **placeholder** (directory + `__init__.py`
only, awaiting its phase).

## kernel/ — trusted core

| File | Status | Purpose |
|------|--------|---------|
| `kernel/db.py` | built | Shared SQLite connection helper (WAL, busy timeout, row factory) |
| `kernel/task_graph.py` | built | `TaskNode` dataclass + `TaskGraph`: CRUD, dependency promotion (`refresh_ready`), dispatch (`pop_ready`), crash recovery (`recover_interrupted`), blocked cascade, retry counter |
| `kernel/event_bus.py` | built | `EventBus`: durable outbox, `publish`/`pending`/`ack`, `dispatch` with retry→dead-letter, `replay`, schema-version registry (`EVENT_SCHEMA`) |
| `kernel/recovery.py` | built | Marker exceptions (`TransientError`, `LogicError`, `EnvironmentFailure`, `HardGatePending`), `FailureClass`, `RecoveryManager` (classify → retry/backoff/terminal; replan + needs_human escalation deferred to Phase 4.5) |
| `kernel/scheduler.py` | built | `Scheduler` (`run_forever`, `run_until_idle`), `WorkerResult`/`SubtaskSpec`/`WorkerContext`, per-dispatch timeout + exception boundary, child spawn, failure handoff |
| `kernel/hard_gate_enforcer.py` | built | `HardGateEnforcer` + `ToolCallSpec` + `HardGateDenied`; approvals table; decisions bound to exact `tool\|action\|target`; no bypass (spec §1) |
| `kernel/__init__.py` | built | Public kernel API re-exports |

## providers/ — LLM access (service)

| File | Status | Purpose |
|------|--------|---------|
| `providers/base.py` | built | `Provider` ABC (`complete`, `health`), `Message`/`Completion`/`ProviderHealth`, error taxonomy mapped onto kernel marker exceptions |
| `providers/local_ollama.py` | built | `LocalOllamaProvider`: stdlib-urllib Ollama client, injectable HTTP fn, maps 429→`RateLimited`, 401/403→`ProviderAuthError`, conn errors→`ProviderUnavailable` |
| `providers/__init__.py` | built | Provider registry: `get_provider(name)`, `registered_providers()` |

## config/

| File | Status | Purpose |
|------|--------|---------|
| `config/routing.yaml` | built (consumer is Phase 4.5) | Routing table: `trivial/moderate/hard/critical` → providers + `verify`/`quorum`; `fallback_order`; NIM key-pool and OpenRouter free-tier policy |

## tests/

| File | Status | Purpose |
|------|--------|---------|
| `tests/test_task_graph.py` | built | Graph CRUD, promotion, recovery, cascade, persistence (9 tests) |
| `tests/test_event_bus.py` | built | Versioning, fan-out, restart durability, retries, dead-letter, replay (8 tests) |
| `tests/test_hard_gate.py` | built | Spec §1: block-until-approval, denial, no event for ungated, vague-call rejection, restart-applied approval, per-target binding (6 tests) |
| `tests/test_recovery.py` | built | Classification, retry caps/terminal, scheduler failure paths, dependent blocking, loop survival (6 tests) |
| `tests/test_scheduler_resume.py` + `tests/_resume_runner.py` | built | **Phase 1 acceptance**: `kill -9` mid-task → restart → resume with no re-prompt, idempotent steps, durable events (1 test) |
| `tests/test_provider.py` | built | Ollama mapping, health, error taxonomy, registry (5 tests) |

## Placeholder packages (created per spec §9, filled in later phases)

| Directory | Phase | Will contain |
|-----------|-------|--------------|
| `memory/` | 2 | working memory, long-term, compression, retrieval, vector store, workspace indexer |
| `tools/` | 3–6 | `registry.py`, `filesystem.py`, `shell.py`, `git.py`, `docker.py`, `browser.py`, `package_managers.py`, `re_static/ghidra_bridge.py`, `security_active/` (hard_gate:true only) |
| `notify/` | 4.5 | `telegram_bot.py`, `discord_bot.py`, `command_worker.py` |
| `permission/` | 4.5 | `modes.py`, `hard_gates.py` (modes cannot bypass the kernel enforcer) |
| `plugins/` | 6.5 | `sdk.py` (manifest schema, loader), `loaded_plugins/` drop-in dir |

## docs/

17 documents per spec §11 — see the README's documentation list.
`agent_handoff.md` is the session-continuity file; read it first when
picking up work.
