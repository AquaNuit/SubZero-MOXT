# Architecture

## The organizing idea: kernel + services

SubZero is a small, trusted **kernel** surrounded by replaceable
**services**. This is the difference between "an agent implementation" and
"a platform other things plug into", and it is what makes the two hardest
requirements achievable:

1. **Durability** — kill the process mid-task, restart, resume with no
   re-prompting. Possible because all state lives in one kernel-owned
   SQLite database and workers are idempotent.
2. **A non-erodable safety gate** — the `hard_gate` enforcement point lives
   inside the kernel, not in any tool, planner, or plugin. Nothing outside
   the kernel can weaken it (see `plugin_api.md` for how this survives
   third-party plugins).

## What the kernel owns

| Module | Responsibility |
|--------|----------------|
| `kernel/task_graph.py` | `TaskNode` persistence, dependency promotion, status machine, crash recovery (`recover_interrupted`), blocked-dependent cascade |
| `kernel/scheduler.py` | The always-running dispatch loop; spawns child tasks from worker output; applies the recovery policy |
| `kernel/event_bus.py` | Durable outbox events: at-least-once delivery, per-consumer fan-out, dead-lettering, replay, schema versioning |
| `kernel/recovery.py` | Failure classification (`transient` / `logic` / `environment` / `hard_gate_pending`) and escalation policy |
| `kernel/hard_gate_enforcer.py` | The spec §1 enforcement point. No bypass exists here. |
| `kernel/db.py` | Shared SQLite connection config (WAL, busy timeout) |

The kernel is deliberately small and changes rarely. Everything else —
providers, tools, memory strategies, notifiers, plugins — is a **service**
the kernel dispatches to.

Between the two sits the `agent/` layer (Phase 4): LLM-driven *workers*
(planner, coding worker) that the scheduler dispatches to. Workers are
services too — they consume the kernel's public interfaces plus the tool
executor, indexer, and memory system, and report failures via the kernel's
marker exceptions. The kernel never imports them.

## The service boundary

One rule, enforced structurally:

> Every kernel→service call passes through a timeout + exception boundary.
> A service failure becomes a `task.failed` event handled by Recovery —
> never an unhandled exception that kills the scheduler process.

In practice (`kernel/scheduler.py::_dispatch`): the worker coroutine is
wrapped in `asyncio.wait_for(...)` inside a broad `except`, and the outcome
is routed to `RecoveryManager.handle`. `_pump` additionally traps anything
that somehow escapes the boundary, logs it, and keeps looping.

The inverse rule keeps the kernel small: **the kernel never imports service
internals.** Services communicate failures to the kernel by raising the
kernel's public marker exceptions (`TransientError`, `LogicError`,
`EnvironmentFailure`, `HardGatePending` from `kernel/recovery.py`).
`providers/base.py` already subclasses these; tools and plugins will too.

## Durability story

All kernel state is one SQLite file (default `state/agent.db`) in WAL mode:

- `tasks` — the persistent task graph (spec §2.1 schema)
- `events`, `consumer_offsets`, `event_attempts`, `dead_letters` — the bus
- `approvals` — hard-gate decisions, keyed by `task_id + tool|action|target`

Because events and offsets are in the same ACID store as the graph, the
at-least-once guarantee "falls out for free" (spec §2.2) — no Redis, no
external broker, nothing else to run on a 16GB machine.

Restart sequence (`Scheduler.run_*`): `recover_interrupted()` re-queues
`running` tasks (their workers died with the process) and `needs_human`
tasks (the approvals table is the source of truth — a re-dispatched worker
re-enters the gate and applies a recorded decision immediately, or keeps
waiting if none exists). Workers must be idempotent: re-execution after a
kill must not duplicate side effects. The acceptance test proves this with
`kill -9` on a real subprocess.

## Concurrency model

One asyncio process. The scheduler dispatches up to `max_concurrency`
(default 4) workers concurrently. SQLite handles the cross-thread cases
(hard-gate approval waits run in short-lived threads with their own
connections; WAL + busy timeout serializes writers). There is no
distributed worker pool yet — multi-agent orchestration in Phase 6 spawns
subagent *processes* coordinated through the same kernel DB.

## Notification architecture (Phase 4.5, designed now)

Telegram/Discord bots are separate processes that only:

- **consume** from the event bus (their own consumer offsets — a bot crash
  cannot lose events or affect the kernel), and
- **write** to the Command Worker (approvals, /pause, /priority).

They never talk to LLM providers or tools directly. This keeps the kernel
loop structurally independent of whether any notifier is alive.
