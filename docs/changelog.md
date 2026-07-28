# Changelog

Format: one entry per working session/phase, newest first. Notable
decisions belong in `agent_handoff.md` too.

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
