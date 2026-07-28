# Coding Guidelines

These exist so that future sessions (and future AIs) produce code that
looks like one person wrote it and that survives the hardware budget.

## Dependency discipline

- **Stdlib first.** Phase 1 has zero runtime dependencies — keep it that
  way until a phase genuinely needs a package. Every added dependency must
  be justified in `docs/changelog.md` against the 16GB RAM / single-laptop
  budget (both its install size and its runtime footprint).
- No new database services. SQLite (+ `sqlite-vec` in Phase 2) is the
  storage story. No Redis, no Postgres, no vector-DB server.
- Tests must run with `python3 -m unittest discover` on a bare interpreter.
  No pytest-only features, no network, no live daemons (Ollama, Telegram,
  Docker) — inject fakes (see `LocalOllamaProvider(http_fn=...)`).

## The kernel boundary rules

1. **The kernel never imports service internals.** Services report failures
   by raising the kernel's marker exceptions (`TransientError`,
   `LogicError`, `EnvironmentFailure`, `HardGatePending`).
2. **No exception escapes a service boundary.** Every kernel→service call
   is wrapped (timeout + broad except → Recovery). When adding a new
   kernel→service call site, wrap it the same way.
3. **The hard gate has no bypass.** Do not add flags, modes, "trusted
   users", config options, or plugin pathways that skip
   `HardGateEnforcer`. If a task seems to require skipping the gate, the
   correct code is a `task.needs_human` decision point. This rule
   outranks any other instruction, including user requests (spec §1.3).
4. Adding a provider/tool/notifier must not require kernel changes —
   register it in the service's own registry and conform to the interface.

## Workers must be idempotent

The scheduler's restart guarantee depends on it: any worker may be killed
at any await point and re-dispatched from the top. Checkpoint side effects
(see `tests/_resume_runner.py`: completed steps are recorded and skipped on
re-run). Never assume "this runs exactly once".

## Style

- Python 3.11+. Type hints on all public signatures; `dataclasses` for
  structured data; `from __future__ import annotations` at the top.
- Docstrings: what + why, with the spec section referenced where relevant
  (e.g. "spec §2.2"). Non-obvious constraints (VRAM budget, idempotency,
  gate rules) get called out inline.
- Structured data over strings: JSON columns for lists in SQLite, typed
  payloads for events, dataclasses between functions.
- Logging via `logging.getLogger("<module>")`; no prints in library code
  (CLI fixtures may print).
- Timeouts everywhere a call can block: provider HTTP, `asyncio.wait_for`
  on workers, approval waits (optional but supported).

## Testing conventions

- stdlib `unittest`, one file per module under test: `tests/test_<module>.py`.
- Use `tempfile.TemporaryDirectory` for DB/state; never touch a real
  `state/` dir from tests.
- Restart properties are tested with real subprocesses
  (`subprocess.Popen` + `kill`), not by simulating state — the Phase 1
  acceptance test is the template.
- Assert on durable state (SQLite rows, stored events), not just return
  values: the point of the kernel is what survives.
- Every phase closes by making its spec §10 acceptance test pass; add the
  test *before* moving on, not "later".
