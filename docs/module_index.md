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

## memory/ — memory & context system (service)

| File | Status | Purpose |
|------|--------|---------|
| `memory/vector_store.py` | built | `Embedder` protocol, `HashEmbedder` (2-probe signed hashing), `VectorStore` namespaced cosine search in SQLite |
| `memory/workspace_indexer.py` | built | File index + symbol graph (ast for Python, regex for 8 languages) + dependency graph; incremental `scan()`; `where_is`/`dependents_of`/`search_code` |
| `memory/long_term.py` | built | `DecisionMemory` (embedded "tried X, outcome Y") + `ProjectMemory` (facts with provenance) |
| `memory/compression.py` | built | `CompressedSummary` structured shape, `HeuristicSummarizer`/`LLMSummarizer` (fallback), `TranscriptStore`, `mid_task_compress` |
| `memory/working_memory.py` | built | Minimal context assembly, `TokenCounter` protocol, `ContextPressureMonitor` (70% threshold) |
| `memory/retrieval.py` | built | `Retriever.gather` merging project/decision/workspace sources |

## tools/ — tool framework (service)

| File | Status | Purpose |
|------|--------|---------|
| `tools/base.py` | built | `Tool` ABC, `ParamSpec` typing, `ToolResult` (+`failure_class`), `ToolExecutor` (validate→gate→run), runner protocol + `async_subprocess_runner` |
| `tools/registry.py` | built | Registration-time interface enforcement; planner `catalog()` |
| `tools/shell.py` | built | Shell commands (cwd, timeout w/ process-group kill) |
| `tools/filesystem.py` | built | read/write/append/list/exists/mkdir |
| `tools/git.py` | built | init/status/log/diff/add/commit/branch; optional commit-author override |
| `tools/python_exec.py` | built | Python code/script in a subprocess |
| `tools/package_managers.py` | built | Distro detection (apt/dnf/pacman/flatpak/snap), command adaptation, dry-run |
| `tools/docker.py` | built | Container management; daemon-down → environment failure |
| `tools/re_static/`, `tools/security_active/` | placeholder | Phase 5.5 (Ghidra bridge, hard_gate:false) / active tooling (hard_gate:true only) |

## tests/

| File | Status | Purpose |
|------|--------|---------|
| `tests/test_task_graph.py` | built | Graph CRUD, promotion, recovery, cascade, persistence (9 tests) |
| `tests/test_event_bus.py` | built | Versioning, fan-out, restart durability, retries, dead-letter, replay (8 tests) |
| `tests/test_hard_gate.py` | built | Spec §1: block-until-approval, denial, no event for ungated, vague-call rejection, restart-applied approval, per-target binding (6 tests) |
| `tests/test_recovery.py` | built | Classification, retry caps/terminal, scheduler failure paths, dependent blocking, loop survival (6 tests) |
| `tests/test_scheduler_resume.py` + `tests/_resume_runner.py` | built | **Phase 1 acceptance**: `kill -9` mid-task → restart → resume with no re-prompt, idempotent steps, durable events (1 test) |
| `tests/test_provider.py` | built | Ollama mapping, health, error taxonomy, registry (5 tests) |
| `tests/test_vector_store.py` | built | Embedder determinism/normalization/ordering, namespacing, overwrite, prefix delete, persistence (6 tests) |
| `tests/test_workspace_indexer.py` | built | **Phase 2 acceptance** (changed-file-only subgraph) + ast symbols, dependency graph, incremental/no-op/remove scans, regex languages (6 tests) |
| `tests/test_compression.py` | built | Structured extraction, JSON roundtrip, compact parent summary, transcript store, mid-task compress, LLM path + fallbacks (8 tests) |
| `tests/test_working_memory.py` | built | Assembly minimality, 70% threshold firing, bounded after compression, retriever 3-source merge (5 tests) |
| `tests/test_memory_integration.py` | built | **Phase 2 acceptance**: 40-turn scheduler task forces 3+ compressions, context bounded after each, structured summaries, externalized transcript (1 test) |
| `tests/test_tools_registry.py` | built | Registration refusals, param coercion, executor flows incl. gated tool through the real enforcer (16 tests) |
| `tests/test_tools_exec.py` | built | Real hermetic shell/fs/git/python_exec runs incl. timeouts + injected runners (15 tests) |
| `tests/test_package_managers.py` | built | Distro detection (5 managers + fallbacks), command adaptation, dry-run, real dpkg path (14 tests) |
| `tests/test_docker.py` | built | Command construction, daemon-down environment failure (6 tests) |
| `tests/test_phase3_acceptance.py` | built | **Phase 3 acceptance**: install (apt+dnf paths), run script, git commit, report — via Scheduler (1 test) |

## Placeholder packages (created per spec §9, filled in later phases)

| Directory | Phase | Will contain |
|-----------|-------|--------------|
| ~~`memory/`~~ | ~~2~~ | **BUILT in Phase 2** — see table above |
| ~~`tools/`~~ | ~~3~~ | **BUILT in Phase 3** (except `browser.py` — Phase 5, `re_static/` + `security_active/` — Phase 5.5) — see table above |
| `notify/` | 4.5 | `telegram_bot.py`, `discord_bot.py`, `command_worker.py` |
| `permission/` | 4.5 | `modes.py`, `hard_gates.py` (modes cannot bypass the kernel enforcer) |
| `plugins/` | 6.5 | `sdk.py` (manifest schema, loader), `loaded_plugins/` drop-in dir |

## docs/

17 documents per spec §11 — see the README's documentation list.
`agent_handoff.md` is the session-continuity file; read it first when
picking up work.
