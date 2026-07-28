# Testing

## Running

```bash
# from the repo root — stdlib only, no installation needed
python3 -m unittest discover -s tests -t . -v
```

Current status: **35 tests, all passing** (also clean under
`-W error::ResourceWarning`).

## Suite map

| File | Proves |
|------|--------|
| `tests/test_task_graph.py` (9) | Graph CRUD, dependency promotion, oldest-first dispatch, crash re-queue, result round-trip, blocked cascade, retry counter, cross-connection persistence |
| `tests/test_event_bus.py` (8) | Schema versioning, per-consumer fan-out, **event survives bus restart**, ack durability, handler retry, dead-letter + unblock, replay by time/type |
| `tests/test_hard_gate.py` (6) | **Spec §1**: gated call blocks until approval with exact action+target in the event; denial prevents execution; ungated calls run silently; vague calls rejected; recorded approval survives restart; approval binds to exact target |
| `tests/test_recovery.py` (6) | Failure classification, transient retry→success path, retry cap→terminal failed, dependent cascade-blocking, unknown exceptions never kill the scheduler loop |
| `tests/test_provider.py` (5) | Ollama request/response mapping, health both ways, 429/401/500/conn-refused error taxonomy, registry |
| `tests/test_scheduler_resume.py` (1) | **Phase 1 acceptance** (see below) |

## Acceptance-test mapping (spec §10)

| Phase 1 criterion | Test |
|-------------------|------|
| Kill/restart mid-task → resume, no user re-prompt | `test_scheduler_resume.py`: runs `tests/_resume_runner.py` as a real subprocess, `kill -9` after ≥2 of 6 steps, restarts, asserts: resume banner (not task-recreation), all 6 steps exactly once (idempotency), single task row `done`, events intact |
| Event survives a bus restart | `test_event_bus.py::test_event_survives_bus_restart_unacked` + the acceptance test's post-kill assertions on the real DB |

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
