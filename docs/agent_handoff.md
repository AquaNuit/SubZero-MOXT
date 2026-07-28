# Agent Handoff

**Read this first when picking up work.** Updated at the end of every
session (spec §11) — it is how continuity works across sessions. Keep it
factual; if it and the code disagree, the code is right (fix this file).

---

## Session 2026-07-28 (night) — Phase 4 complete

### Current implementation status

**Phase 4 of 7 DONE and acceptance-tested** (132/132 stdlib-unittest tests
pass: 121 from Phases 1–3 + 11 new, ~5.4s). Phase 4.5 is next — see "Next
recommended task".

### Modules completed this session

- `agent/planner.py` — `Planner` over the Phase 1 Provider interface:
  strict-JSON system prompt, defensive parse (JSON-in-prose extraction,
  per-edit path/find/replace validation, required test_command), one
  stricter retry, then `LogicError`; `replan_with_failure` carries the
  failure output + demands a genuinely different fix.
- `agent/coding_worker.py` — `CodingWorker` (Scheduler-compatible
  `worker(task, ctx)`): indexer-first (`scan` + `where_is` on goal
  identifiers BEFORE any provider call; locations → prompt) →
  WorkingMemory assemble → plan → bounded edit/test/debug loop (exact
  find/replace via ToolExecutor; shell test runs; failure-context replan;
  `LogicError` on exhaustion) → close-out (post-edit re-index, structured
  compression, externalized transcript, decision recorded). `trace` hook
  for ordering assertions.
- Tests: `test_planner.py` (7), `test_coding_agent.py` (4) — incl. the
  acceptance: real seeded-bug repo fixed E2E; indexer-first proven by
  trace order + `ops.py:8` in first prompt + read-set == {ops.py}; fix
  verified by real unittest rerun OUTSIDE the agent.
- Docs: status/roadmap/changelog/module_index/testing/known_issues/
  architecture/README updated.

### Architecture decisions made (and why)

1. **New top-level `agent/` package** for LLM-driven workers — sits
   between kernel and services; kernel never imports it (§9 tree extended,
   documented in module_index + architecture).
2. **Edits are exact find/replace, must match exactly once** — small local
   models produce them far more reliably than unified diffs, and a
   mismatch is clean replan input instead of a corrupt file. Ambiguous
   `find` (2+ matches) bounces back to the planner.
3. **Iteration exhaustion / unparseable plans raise `LogicError`** →
   Recovery marks task failed with failure_class=logic (Phase 1 policy).
   Phase 4.5 turns this into capped replans + needs_human escalation —
   the worker already provides the replan machinery Recovery will call.
4. **Scripted-provider testing pattern** (testing.md rule 8): the model is
   playback; EVERYTHING else runs real — indexer, filesystem, shell test
   runs, scheduler, transcripts, decision memory. This is how LLM-in-the-
   loop phases stay hermetic in the no-network sandbox.
5. **`trace` hook on workers** — ordering/read-set properties
   ("indexer before provider", "no repo scan") are asserted on an
   explicit event trace rather than inferred from mocks.

### Known issues / gotchas for next session

- New entries 0.i (only scripted-provider runs so far — real-model prompt
  quality unverified; expect parse retries; consider Ollama
  `format: json`) and 0.ii (whole-block edit granularity). Full list in
  known_issues.md.
- **Phase 4.5 needs network secrets on AT's laptop** (Telegram/Discord
  tokens, NIM API keys) — keep the sandbox build hermetic: bots behind
  injectable API clients (long-poll loop + command handlers fully
  testable), provider HTTP behind injected transports (pattern:
  `LocalOllamaProvider(http_fn=...)`).
- Phase 4.5 also upgrades Recovery: logic → capped replan (call
  `Planner.replan_with_failure` via the worker), environment/exhausted →
  needs_human escalation through the new notifier. Circuit breakers per
  tool/provider + `budget.warning` events (event schema already reserved).
- OpenRouter free tier: router must honor `free_tier_reserved_for:
  critical` from config/routing.yaml (adds PyYAML dep — justify in
  changelog, or write a mini YAML subset parser… PyYAML is fine).

### Performance observations

- 132 tests in ~5.4s. Coding-agent tests spawn real subprocesses
  (unittest runs) — still fast. No new steady-state footprint; VRAM
  untouched (first real VRAM use comes when Ollama runs on the laptop).

### Next recommended task

**Phase 4.5: Notifier/Command layer + NIM key pool + remaining providers
+ Recovery classification upgrade** (spec §3, §4, §2.3).

Suggested order:
1. `providers/router.py` + PyYAML — routing table reader, task classifier
   (heuristic first), health-based reweighting tables in the kernel DB,
   verifier/critic pass for hard/critical, quorum=2.
2. `providers/nim.py` + `ProviderKeyPool` (round-robin, 40rpm/key window,
   429 per-key backoff); `providers/openrouter.py`,
   `providers/opencode_zen.py` — all with injected transports.
3. `notify/command_worker.py` — approval queue → enforcer.approve/deny;
   then `notify/telegram_bot.py` + `notify/discord_bot.py` as thin bus
   consumers + command writers behind injectable API clients.
4. `kernel/recovery.py` upgrade — logic → capped replan (wire to worker),
   environment/exhausted → needs_human escalation (emit event; set task
   needs_human); per-tool/provider circuit breakers emitting
   `budget.warning`/`circuit.open`.
5. `permission/modes.py` + audit log table (spec §8) on the executor.

Acceptance (spec §10): pull the network cable on the main NIM key
mid-task → pool fails over (simulate with injected transports: key A
starts failing 429/conn, assert key B takes over and task completes);
approve/deny a needs_human from the (fake) Telegram client end-to-end;
force transient vs logic failure → assert different recovery paths
(retry vs replan/nees_human). **Do not start Phase 5 in the same session
unless Phase 4.5's acceptance passes.**

---

## Session 2026-07-28 (late) — Phase 3 complete

### Current implementation status

**Phase 3 of 7 DONE and acceptance-tested** (121/121 stdlib-unittest tests
pass: 68 from Phases 1–2 + 53 new, ~5s). Phase 4 is next — see "Next
recommended task".

### Modules completed this session

- `tools/base.py` — `Tool` ABC (name/description/`ParamSpec` params/
  explicit `hard_gate` bool), `ToolResult` with recovery `failure_class`
  + `raise_for_failure()`, `ToolExecutor` (validate → gate → run; the ONLY
  tool call path; nothing raises across it), runner protocol +
  `async_subprocess_runner` (process-group kill on timeout).
- `tools/registry.py` — registration-time interface enforcement (refuses
  missing gate flag/name/desc, duplicates), planner-facing `catalog()`.
- `tools/shell.py`, `filesystem.py`, `git.py`, `python_exec.py` — the
  order-1 tools, all `hard_gate: false`, all hermetically tested with REAL
  runners (tmp dirs; git tested against real repos).
- `tools/package_managers.py` — distro detection (/etc/os-release ID →
  ID_LIKE → flatpak/snap binary probe → unknown), command adaptation for 5
  managers, dry-run (executes nothing, returns the exact command).
- `tools/docker.py` — ps/images/pull/run/stop/logs/remove; daemon-down →
  structured `environment` failure.
- Tests: `test_tools_registry.py` (16), `test_tools_exec.py` (15),
  `test_package_managers.py` (14), `test_docker.py` (6),
  `test_phase3_acceptance.py` (the spec §10 acceptance: install via
  apt+dnf code paths, real script exec, real git commit, bus-reported,
  through the Scheduler).
- Docs: tool_api.md rewritten as-built; status/roadmap/changelog/
  module_index/testing/known_issues/README updated.

### Architecture decisions made (and why)

1. **Executor gate kwargs are `gate_action`/`gate_target`** — the
   acceptance test caught `action` colliding between executor kwargs and
   tool params (filesystem/git/package all use `action`). Gate metadata is
   executor-level; tool params pass through untouched.
2. **A gated tool on an executor WITHOUT an enforcer is refused, never
   run** (`environment` failure). The kernel enforcer remains the only
   gate implementation; the executor is its only caller — the §1 invariant
   now holds at the single call path, not per-tool.
3. **Failure classification conventions for tools**: bad params/unknown
   action/missing file → `logic` (planner's fault); timeout → `transient`;
   permission/daemon-down/failed install → `environment`;
   `is_installed` false → `logic` (it's an answer, not an error).
4. **Distro multi-coverage is proven at the code-path level** in the
   sandbox (one distro, no root): real os-release strings + recording
   runners, verbatim command assertions. Same worker runs unchanged on
   real multi-distro machines.
5. **`sudo` baked into package commands** — fine for a single-user laptop
   with passwordless sudo; failures classify `environment` (known_issue
   0.c).

### Known issues / gotchas for next session

- New entries 0.a–0.d (audit log hook pending Phase 4.5, dry-run scope,
  sudo assumption, docker hermetic-only). Full list in known_issues.md.
- **Phase 4 is the first phase with a live model in the loop.** Keep every
  test hermetic: fake providers implementing the Phase 1 `Provider`
  interface (see `tests/test_provider.py` and `LLMSummarizer` tests for
  the pattern). The planner worker must consume: WorkspaceIndexer
  (`where_is`/`dependents_of` BEFORE asking the model where code lives —
  Phase 4 acceptance requires indexer-first navigation), ToolExecutor,
  WorkingMemory/Retriever, ContextPressureMonitor.
- `LLMSummarizer` is sync-only; the planner worker is async — either use
  HeuristicSummarizer in the worker or add `async_summarize` (known_issue 0).

### Performance observations

- 121 tests in ~5.0s (real subprocess spawning in tool tests added ~1s).
  Tool calls themselves: subprocess spawn overhead only. No new
  steady-state footprint; VRAM still untouched.

### Next recommended task

**Phase 4: Coding agent workflow (plan/edit/test/debug loop)**, spec §6
order + §10. This is the payoff phase: an LLM-driven worker that fixes a
seeded bug end-to-end unattended.

Suggested shape:
1. `agent/` (new top-level package — update §9 tree in docs) with
   `planner.py` (provider → structured plan: steps + files),
   `coding_worker.py` (the Scheduler `worker` impl): assemble via
   WorkingMemory → indexer queries first (log/emit how many LLM calls
   were avoided) → plan → edit loop (filesystem/python_exec/shell tools
   via ToolExecutor) → run tests → debug on failure (bounded iterations)
   → compress on close (Phase 2 machinery).
2. Everything behind fake providers in tests: script the planner's
   outputs (plan JSON, edit diffs, test-fix iterations).
3. Acceptance (spec §10): seeded bug in a small tmp repo fixed
   end-to-end by the scheduler-driven worker, using `where_is`/
   `dependents_of` instead of a fresh full-repo scan — assert indexer
   queries happened BEFORE the first provider call and that no full-tree
   read occurred (instrument both).

Write the acceptance test first. **Do not start Phase 4.5 in the same
session unless Phase 4's acceptance passes.**

---

## Session 2026-07-28 (evening) — Phase 2 complete

### Current implementation status

**Phase 2 of 7 DONE and acceptance-tested** (68/68 stdlib-unittest tests
pass: 35 Phase 1 + 33 Phase 2, ~4s). Phase 3 is the next task — see "Next
recommended task" below.

### Modules completed this session

- `memory/vector_store.py` — `Embedder` protocol; `HashEmbedder`
  (deterministic, 2-probe signed feature hashing, dim 512 default);
  `VectorStore` (namespaced float32 blobs in kernel DB, Python cosine,
  upsert/delete/prefix-delete/search/count/refs).
- `memory/workspace_indexer.py` — file index (sha256-triggered re-embed
  only), Python `ast` symbol extraction (function/class/method + line +
  signature), regex extractors for JS/TS/Go/Rust/C/C++/Java/Bash,
  `workspace_imports` dependency graph, incremental `scan()` →
  `ScanReport`, queries `where_is`/`imports_of`/`dependents_of`/
  `search_code`.
- `memory/long_term.py` — `DecisionMemory` (rows + embeddings),
  `ProjectMemory` (key/value facts + provenance).
- `memory/compression.py` — `CompressedSummary` (structured JSON shape),
  `HeuristicSummarizer`, `LLMSummarizer` (hard fallback), `TranscriptStore`
  (`state/transcripts/<task_id>.jsonl`), `mid_task_compress`.
- `memory/working_memory.py` — `WorkingMemory.assemble` (goal + parent
  summary + retrieval only), `TokenCounter` protocol +
  `HeuristicTokenCounter`, `ContextPressureMonitor` (70% default).
- `memory/retrieval.py` — `Retriever.gather` (project + decision +
  workspace merge, deduped, capped).
- Tests: `test_vector_store.py` (6), `test_workspace_indexer.py` (6, incl.
  changed-subgraph acceptance), `test_compression.py` (8),
  `test_working_memory.py` (5), `test_memory_integration.py` (1,
  bounded-context acceptance through the real scheduler).
- Docs: memory_system.md + context_management.md rewritten as-built;
  status/roadmap/changelog/module_index/known_issues/testing/README updated.

### Architecture decisions made (and why)

1. **No vector-DB server, no `sqlite-vec` yet** — Python cosine over
   float32 blobs in the same SQLite file. Right-sized for a single-user
   workspace; `sqlite-vec` is a Phase 7 optimization behind the existing
   interface if profiling demands it.
2. **`HashEmbedder` as the default embedder** — zero deps, zero VRAM,
   deterministic: every retrieval test is hermetic. Real embedding model
   (Ollama `nomic-embed-text` class, <1GB VRAM to protect the 6GB budget)
   plugs into the 2-member `Embedder` protocol on AT's laptop without
   caller changes. NOTE: keyword-grade recall only — paraphrases don't
   match (known_issues 0.1).
3. **2 probes per token in the hash embedder** — a single bucket collision
   was observed zeroing a shared term (real bug found by a failing test);
   2 probes makes that ~impossible. Retrieval tests must use 2+ overlapping
   tokens and dim ≥1024 when overlap is small (testing.md rule 7).
4. **Python symbols via stdlib `ast`; tree-sitter deferred** — it's a new
   dependency; regex extractors cover 8 other languages conservatively.
   Upgrade path: same tables, swap `_extract`.
5. **`LLMSummarizer` has a hard fallback to heuristic** on ANY error
   (provider down, non-JSON output) — compression must never kill a
   running task. It's also sync-only (`asyncio.run`); async path is a
   Phase 4 item (known_issues 0).
6. **`as_result_summary` caps the result portion first** — structured
   fields (Decisions/Files/Open) must survive truncation because
   downstream retrieval filters on them (found via acceptance-test
   failure).

### Known issues / gotchas for next session

- New entries 0–0.3 in `known_issues.md` (sync-only LLMSummarizer,
  keyword-grade embedder, heuristic token counter, heuristic dep-graph
  resolution). Phase 1 items 1–7 unchanged.
- Phase 3 introduces the tool registry: enforce `hard_gate` presence at
   registration time (contract in `docs/tool_api.md`), and remember the
  enforcer from Phase 1 is the ONLY gate — tools declare, kernel decides.
- The sandbox still has no network/pip: keep everything stdlib; shell tool
  tests must be hermetic (tmp dirs, no real package installs — fake the
  distro detection + command runner via injection, same pattern as
  `LocalOllamaProvider(http_fn=...)`).

### Performance observations

- 68 tests in ~4.0s. Indexer scan of small trees is instant; brute-force
  cosine over a few hundred chunks is sub-ms. No new steady-state memory
  (all state in the one SQLite file + transcript JSONLs).
- VRAM still untouched. Embedding-model budget reserved: <1GB when the
  real model lands (6GB total, Ollama LLM ~4.7GB).

### Next recommended task

**Phase 3: Linux tools + execution engine**, per spec §6 order 1–3 and the
contract in `docs/tool_api.md`.

Suggested order:
1. `tools/registry.py` — name→tool registry enforcing the interface at
   registration (name/description/typed parameters/`hard_gate` required;
   malformed tools refused loudly).
2. `tools/base.py` — `Tool` ABC + `ToolResult` + parameter typing; wire
   every call through `kernel.hard_gate_enforcer` for gated tools.
3. `tools/filesystem.py`, `tools/shell.py`, `tools/git.py`,
   `tools/python_exec.py` — all `hard_gate: false` (local reads/writes);
   command runners injectable for hermetic tests.
4. `tools/package_managers.py` — distro detection (apt/dnf/pacman/
   flatpak/snap) behind an injectable detector + runner; dry-run support.
5. `tools/docker.py` — container management; skip-when-no-daemon pattern
   for tests (inject client).

Acceptance before Phase 4 (spec §10): agent installs a package, runs a
script, reports result on 2+ distros — in the sandbox that means 2+
*detected-distro code paths* proven hermetically (fake apt + fake dnf),
plus a real end-to-end run of filesystem/shell/git/python tools driven
through the Scheduler. Write the acceptance test first. **Do not start
Phase 4 in the same session unless Phase 3's acceptance passes.**

---

## Session 2026-07-28 — Phase 1 complete

### Current implementation status

**Phase 1 of 7 DONE and acceptance-tested** (35/35 stdlib-unittest tests
pass, `python3 -m unittest discover -s tests -t . -v`, ~3.7s). Phase 2 is
the next task — see "Next recommended task" below. Full per-phase table:
`implementation_status.md`.

### Modules completed this session

- `kernel/db.py` — shared SQLite connect (WAL, busy timeout, Row factory).
- `kernel/task_graph.py` — `TaskNode`/`TaskGraph`: spec §2.1 schema,
  dependency promotion, `pop_ready`, `recover_interrupted`, blocked
  cascade, retry counter.
- `kernel/event_bus.py` — durable outbox bus: publish/pending/ack,
  `dispatch` (retry → dead-letter), `replay`, `EVENT_SCHEMA` (7 types, v1).
- `kernel/recovery.py` — marker exceptions + `FailureClass` +
  `RecoveryManager` (transient retry w/ exp backoff cap 60s, max_retries 3;
  logic/environment terminal for now).
- `kernel/scheduler.py` — asyncio loop (`run_forever`/`run_until_idle`),
  concurrency 4, per-dispatch timeout+exception boundary, child spawn,
  recovery handoff, events emitted on every transition.
- `kernel/hard_gate_enforcer.py` — spec §1 enforcement: blocks gated calls
  until recorded approval keyed by `tool|action|target`; approvals table;
  restart-safe; **no bypass, ever**.
- `providers/base.py`, `providers/local_ollama.py`, `providers/__init__.py`
  — `Provider` ABC, error taxonomy onto kernel markers, stdlib Ollama
  client (injectable `http_fn`), registry.
- `config/routing.yaml` — full §4 routing table (unread until Phase 4.5
  router — intentional).
- `tests/` — 6 test files, 35 tests incl. the `kill -9` resume acceptance
  test (`test_scheduler_resume.py` + `_resume_runner.py` fixture).
- `docs/` — complete §11 doc set + README.md.
- Scaffolding per §9: `memory/`, `tools/` (+`re_static/`,
  `security_active/`), `notify/`, `permission/`, `plugins/`
  (+`loaded_plugins/`).

### Architecture decisions made (and why)

1. **Repo root IS the `agent-framework/` tree** (no nested dir) — plain
   imports (`from kernel.scheduler import Scheduler`); spec §9 shows the
   tree, the repo realizes it.
2. **SQLite outbox for the event bus, not Redis** — spec §2.2's explicit
   option; at-least-once "falls out for free" when events share ACID
   storage with the graph; nothing extra to run on 16GB RAM.
3. **Zero runtime dependencies for Phase 1** (stdlib only, incl. tests via
   `unittest` and HTTP via `urllib`) — sandbox had no pytest/httpx/pip
   access, and it turned out to be the right call anyway for the footprint
   budget. PyYAML arrives with the Phase 4.5 router (justify in changelog).
4. **Recovery classification contract via kernel marker exceptions**
   (`TransientError`/`LogicError`/`EnvironmentFailure`/`HardGatePending`)
   that services subclass — the kernel never imports service internals.
   Logic→replan & environment→needs_human escalation **deferred to Phase
   4.5** (needs planner + notifier); `RecoveryAction` already has the enum
   values so the upgrade is policy-only.
5. **Hard-gate decisions bind to `tool|action|target`** and live in the
   kernel DB — approving one target never approves another, and a restart
   neither loses nor re-asks a decision. The enforcer was built in Phase 1
   (not 5.5) because §1 is non-negotiable and kernel-owned.
6. **`needs_human` is a persisted status, not an in-memory pause** —
   approval waits survive `kill -9`; `recover_interrupted` re-queues them
   and the re-dispatched worker applies any recorded decision via the gate.
7. **Workers must be idempotent** (checkpoint side effects, skip completed
   steps on re-dispatch) — this is what makes the restart guarantee real.
   Enforced by convention + the acceptance test pattern; call it out in
   review of every new worker.

### Known issues / gotchas for next session

- Numbered list in `known_issues.md`. The ones most likely to bite during
  Phase 2: (2) backoff holds a dispatch slot; (5) no LLM worker yet —
  Phase 2 memory modules must therefore be designed to be *called by* a
  future worker, not to drive anything themselves.
- The sandbox used for the Phase 1 build has no Ollama daemon, no network
  for pip — design Phase 2 tests to be hermetic (inject embedding model /
  fake vectors; see `LocalOllamaProvider(http_fn=...)` for the pattern).
- `git log` inside the Moxt workspace root is synthetic; the real history
  is this GitHub repo. Commit here with meaningful messages — they are the
  audit trail.

### Performance observations

- Suite: 35 tests in ~3.7s on a shared sandbox CPU; memory negligible;
  zero warnings under `-W error::ResourceWarning`.
- Idle scheduler ≈ zero CPU (sleep-based poll). DB: one small SQLite file.
- VRAM untouched so far; Ollama default (`qwen2.5-coder:7b` q4 ≈ 4.7GB)
  leaves ~1.3GB headroom on the 6GB target — keep that margin in mind when
  Phase 2 picks a local embedding model (small! e.g. nomic-embed-text
  class, <1GB, or hash-based embeddings to start).

### Next recommended task

**Phase 2: Memory/context system + Workspace Indexer**, per spec §5/§5.1
and the designs in `context_management.md` / `memory_system.md`.

Suggested order inside the phase:
1. `memory/vector_store.py` — sqlite-vec if installable, else a stdlib
   fallback (embedding stored as blob, cosine in Python) behind one
   interface; decide and record the choice in `changelog.md`.
2. `memory/workspace_indexer.py` — file index (path/language/hash/
   embedded-at) + mtime/hash poll + changed-file-only re-embedding. Then
   the tree-sitter symbol graph (tree-sitter is a new dep — justify it;
   if the sandbox can't install it, build the file index first and stub
   the symbol graph behind the same interface with a clear TODO).
3. `memory/compression.py` + `memory/working_memory.py` — structured
   subtask summaries (decisions[]/files_touched[]/open_questions[]/result),
   70%-threshold mid-task compression.
4. `memory/retrieval.py` + `memory/long_term.py` — top-k assembly across
   workspace/decision/project memory.

Acceptance before Phase 3 (spec §10): a task long enough to force a
compression pass keeps active context bounded; touching one indexed file
updates only that file's subgraph. Write those tests first — same
discipline as Phase 1. **Do not start Phase 3 tools in the same session
unless Phase 2's acceptance tests pass.**

---
