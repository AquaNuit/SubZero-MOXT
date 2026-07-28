# Testing

## Running

```bash
# from the repo root — stdlib only, no installation needed
python3 -m unittest discover -s tests -t . -v
```

Current status: **121 tests, all passing** (also clean under
`-W error::ResourceWarning`).

## Suite map

| File | Proves |
|------|--------|
| `tests/test_task_graph.py` (9) | Graph CRUD, dependency promotion, oldest-first dispatch, crash re-queue, result round-trip, blocked cascade, retry counter, cross-connection persistence |
| `tests/test_event_bus.py` (8) | Schema versioning, per-consumer fan-out, **event survives bus restart**, ack durability, handler retry, dead-letter + unblock, replay by time/type |
| `tests/test_hard_gate.py` (6) | **Spec §1**: gated call blocks until approval with exact action+target in the event; denial prevents execution; ungated calls run silently; vague calls rejected; recorded approval survives restart; approval binds to exact target |
| `tests/test_recovery.py` (6) | Failure classification, transient retry→success path, retry cap→terminal failed, dependent cascade-blocking, unknown exceptions never kill the scheduler loop |
| `tests/test_provider.py` (5) | Ollama request/response mapping, health both ways, 429/401/500/conn-refused error taxonomy, registry |
| `tests/test_vector_store.py` (6) | Hash-embedder determinism/normalization/similarity ordering, namespace isolation, upsert overwrite, prefix delete, persistence |
| `tests/test_workspace_indexer.py` (6) | ast symbol extraction (function/class/method + lines), import graph + `dependents_of`, `search_code` ranking, no-op rescan touches nothing, **changed-file-only subgraph (acceptance)**, removal leaves no trace, regex-language extraction |
| `tests/test_compression.py` (8) | Structured field extraction, JSON roundtrip, compact parent summary keeps structured fields, transcript store roundtrip, mid-task compress keeps goal+tail, no-op when short, LLM path parses JSON, LLM failure/non-JSON falls back (never crashes) |
| `tests/test_working_memory.py` (5) | Assembly minimality/order, 70% threshold firing, `compress_if_needed` bounds usage, retriever merges project+decision+workspace, empty sources |
| `tests/test_memory_integration.py` (1) | **Phase 2 acceptance** (see below) |
| `tests/test_tools_registry.py` (16) | Registration refusals (no gate flag/name/desc/duplicates), param coercion (bool≠int, unknown/missing), executor: unknown tool, param errors, ungated run, exception→structured result, **gated tool refused without enforcer**, gated approve/deny through the real enforcer, vague gate call rejected |
| `tests/test_tools_exec.py` (15) | Real hermetic runs: shell echo/failure/timeout/cwd/stderr/injected runner; filesystem full cycle + error classes; python_exec real 42/exception/timeout/script/exactly-one; git real init→add→commit→log→branch + command construction |
| `tests/test_package_managers.py` (14) | os-release parsing, ID/ID_LIKE/flatpak/snap/unknown detection, all 5 managers' commands, quoting, errors, install flows (apt+dnf), dry-run executes nothing, failure classes, **real `dpkg -s bash` on this machine** |
| `tests/test_docker.py` (6) | Daemon-unavailable→environment, ps/images/run/pull/stop/logs construction, param errors |
| `tests/test_phase3_acceptance.py` (1) | **Phase 3 acceptance** (see below) |
| `tests/test_scheduler_resume.py` (1) | **Phase 1 acceptance** (see below) |

## Acceptance-test mapping (spec §10)

| Phase 1 criterion | Test |
|-------------------|------|
| Kill/restart mid-task → resume, no user re-prompt | `test_scheduler_resume.py`: runs `tests/_resume_runner.py` as a real subprocess, `kill -9` after ≥2 of 6 steps, restarts, asserts: resume banner (not task-recreation), all 6 steps exactly once (idempotency), single task row `done`, events intact |
| Event survives a bus restart | `test_event_bus.py::test_event_survives_bus_restart_unacked` + the acceptance test's post-kill assertions on the real DB |
| **Phase 2 criterion** | Test |
| Long task forces a compression pass; active context stays bounded | `test_memory_integration.py`: 40-turn task through the real scheduler in a 400-token window; asserts ≥3 compressions fired, threshold was genuinely crossed, every post-compression ratio < threshold, summaries structured, transcript externalized (`full_log_ref` set) |
| Indexer updates only the changed file's subgraph | `test_workspace_indexer.py::test_acceptance_only_changed_files_subgraph_updates`: modifies one file, asserts only its chunks re-embedded, other files' `embedded_at` + chunk refs untouched |
| **Phase 3 criterion** | Test |
| Install a package, run a script, report result, on 2+ distros | `test_phase3_acceptance.py`: scheduler-driven worker installs `htop` through the **apt and dnf production code paths** (real os-release strings, verbatim adapted commands asserted), writes + really executes a diagnostic script, commits it to a real git repo, reports via the event bus. Sandbox has one distro/no root — multi-distro coverage is at the code-path level, documented in the test |

## Rules for future tests

1. **No live external services.** No network, no Ollama daemon, no
   Telegram, no Docker daemon. Inject fakes at the boundary
   (`LocalOllamaProvider(http_fn=...)` is the template).
2. **Restart properties use real subprocesses.** Don't simulate a crash by
   mutating state; `Popen` + `kill` + relaunch, then assert on the durable
   store. Simulated restarts miss the bugs that matter (open fds, WAL
   recovery, partial writes).
3. **Assert on durable state.** SQLite rows and stored events are the
   product's promises; return values are incidental.
4. **Temp dirs everywhere** (`tempfile.TemporaryDirectory`); tests never
   touch a real `state/` directory.
5. **Threads are fine, races are not.** The hard-gate tests use real
   threads with generous join timeouts; poll conditions with deadlines
   (`_wait_for`), never fixed sleeps where a condition exists.
6. New modules land with their test file in the same commit; a phase is
   done when its §10 acceptance test passes, not before.
7. **Retrieval tests must not depend on hash-collision luck.** The
   `HashEmbedder` is keyword-based; use 2+ overlapping tokens between
   query and target text, and dimension ≥1024 when overlap is small
   (see `tests/test_working_memory.py`).
