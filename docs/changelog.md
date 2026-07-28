# Changelog

Format: one entry per working session/phase, newest first. Notable
decisions belong in `agent_handoff.md` too.

## [Phase 3] — 2026-07-28

Linux tools + execution engine. Still zero runtime dependencies; 53 new
tests (121 total), all passing.

### Added
- `tools/base.py` — `Tool` ABC, `ParamSpec` typed params with coercion,
  `ToolResult` carrying recovery `failure_class`, `ToolExecutor` (single
  call path: validate → gate → run; nothing raises across the boundary),
  injectable runner protocol + `async_subprocess_runner` (process-group
  kill on timeout).
- `tools/registry.py` — registration-time enforcement (explicit
  `hard_gate` bool, unique name, description, well-formed params —
  violations refused loudly) + planner-facing `catalog()`.
- `tools/shell.py`, `tools/filesystem.py`, `tools/git.py`,
  `tools/python_exec.py` — the order-1 tools (all `hard_gate: false`).
- `tools/package_managers.py` — distro detection (/etc/os-release ID →
  ID_LIKE → flatpak/snap probe), command adaptation for 5 managers,
  dry-run mode (spec §8).
- `tools/docker.py` — container management; daemon unavailability is a
  structured `environment` failure.
- Tests: `test_tools_registry.py` (16: registration refusals, param
  typing, executor incl. full gated flow through the real enforcer),
  `test_tools_exec.py` (15: real hermetic shell/fs/git/python runs),
  `test_package_managers.py` (14: detection, commands, dry-run, real
  dpkg check), `test_docker.py` (6), `test_phase3_acceptance.py` (the
  spec §10 acceptance, scheduler-driven).

### Decisions
- Executor gate kwargs are `gate_action`/`gate_target` so tool params may
  be named `action`/`target` (collision found by the acceptance test).
- A gated tool called on an executor WITHOUT an enforcer is refused
  (`environment` failure) — never executed. The gate has exactly one
  implementation (kernel enforcer); the executor is its only caller.
- `is_installed` failure classifies as `logic` ("not there", an answer),
  failed install as `environment` (mirror/network/permissions).
- Phase 3 acceptance runs 2-distro coverage as production code paths
  (real detection strings + recording runners) since the sandbox has one
  distro and no root — documented in the test.

## [Phase 2] — 2026-07-28

Memory/context system + workspace indexer. Still zero runtime dependencies;
33 new tests (68 total), all passing.

### Added
- `memory/vector_store.py` — namespaced embeddings in the kernel SQLite DB
  (float32 blobs, Python cosine); `Embedder` protocol; `HashEmbedder`
  (deterministic stdlib embedder, 2-probe signed feature hashing).
- `memory/workspace_indexer.py` — file index (re-embed on hash change
  only), Python `ast` symbol graph + regex extractors for 8 languages,
  import/dependency graph, incremental `scan()` with `ScanReport`,
  `where_is`/`imports_of`/`dependents_of`/`search_code` queries.
- `memory/long_term.py` — `DecisionMemory` (rows + embedded) and
  `ProjectMemory` (facts with provenance).
- `memory/compression.py` — `CompressedSummary` structured shape,
  `HeuristicSummarizer`, `LLMSummarizer` (hard fallback to heuristic),
  `TranscriptStore` (external JSONL transcripts, `full_log_ref` wired),
  `mid_task_compress`.
- `memory/working_memory.py` — minimal assembly rule, `TokenCounter`
  protocol + heuristic counter, `ContextPressureMonitor` (70% threshold).
- `memory/retrieval.py` — `Retriever.gather` merging project/decision/
  workspace sources (deduped, capped).
- Tests: `test_vector_store.py`, `test_workspace_indexer.py` (incl. the
  changed-file-only-subgraph acceptance), `test_compression.py`,
  `test_working_memory.py`, `test_memory_integration.py` (the
  bounded-context acceptance, run through the Phase 1 scheduler).

### Decisions
- No vector-DB server and no `sqlite-vec` yet: Python cosine over blobs is
  enough at this scale; `sqlite-vec` is a Phase 7 optimization behind the
  existing interface if profiling demands it.
- `HashEmbedder` is the default embedder to keep tests hermetic; the real
  embedding model (Ollama, <1GB VRAM) plugs into the same protocol later.
- Python symbols via stdlib `ast`; tree-sitter deferred (it's a new dep —
  the regex extractors cover other languages until it's justified).
- `LLMSummarizer` never fails a task: any provider/parse error falls back
  to the heuristic summarizer.

## [Phase 1] — 2026-07-28

Initial build. Zero runtime dependencies (Python 3.11+ stdlib only);
35-test stdlib-unittest suite, all passing.

### Added
- **Kernel** (`kernel/`):
  - `task_graph.py` — SQLite-persisted task graph (spec §2.1 schema),
    dependency promotion, crash recovery, blocked-dependents cascade.
  - `event_bus.py` — durable outbox event bus: at-least-once delivery,
    per-consumer fan-out, dead-lettering, replay, `schema_version` on every
    event (7 initial event types).
  - `scheduler.py` — asyncio dispatch loop with per-task timeout +
    exception boundary, child-task spawning, recovery handoff.
  - `recovery.py` — failure classification contract (marker exceptions) +
    transient retry policy (capped, exponential backoff).
  - `hard_gate_enforcer.py` — spec §1 enforcement point: `hard_gate: true`
    calls never execute without an explicit recorded approval naming the
    exact action + target. No bypass.
  - `db.py` — shared SQLite config (WAL, busy timeout).
- **Providers** (`providers/`): `Provider` ABC, error taxonomy mapped to
  kernel marker exceptions, `LocalOllamaProvider` (injectable transport),
  name-based registry.
- **Config**: `config/routing.yaml` — full routing table per spec §4
  (consumed by the Phase 4.5 router).
- **Scaffolding**: `memory/`, `tools/` (+`re_static/`, `security_active/`),
  `notify/`, `permission/`, `plugins/` (+`loaded_plugins/`) per spec §9.
- **Tests**: 35 tests including the Phase 1 acceptance test
  (`kill -9` mid-task → restart → resume, no re-prompt).
- **Docs**: full `docs/` set per spec §11.

### Decisions
- Repo root *is* the `agent-framework/` tree (no nested package prefix).
- SQLite outbox chosen over Redis for the bus (spec §2.2's "falls out for
  free" option; nothing extra to run on 16GB RAM).
- Recovery logic→replan / environment→needs_human escalation deferred to
  Phase 4.5 (needs planner + notifier); structure already in place.
