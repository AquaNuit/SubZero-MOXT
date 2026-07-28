# Agent Handoff

**Read this first when picking up work.** Updated at the end of every
session (spec §11) — it is how continuity works across sessions. Keep it
factual; if it and the code disagree, the code is right (fix this file).

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
