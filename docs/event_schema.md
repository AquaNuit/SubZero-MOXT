# Event Schema

The event bus (`kernel/event_bus.py`) is a first-class kernel module with
real guarantees: durable outbox, at-least-once delivery, per-consumer
fan-out, dead-lettering, and replay. This document is the contract for
event payloads. The machine-readable version registry is `EVENT_SCHEMA` in
`kernel/event_bus.py` — **keep the two in sync.**

## Versioning rules

1. Every event carries `schema_version` (integer, per type).
2. Add fields by **extending** payloads; bump `schema_version` when you do.
3. **Never repurpose an existing field's meaning** — that's what lets old
   consumers (the Telegram bot, a plugin written against v1) keep working.
4. Consumers must ignore fields and event types they don't know.
5. Removing a field requires a new event type, not a new version.

## Event types (all currently version 1)

### `task.started` — v1
Emitted by: scheduler, on every dispatch (including re-dispatch after
retry or crash recovery).
Consumers: notifiers, debugging.

| Field | Meaning |
|-------|---------|
| `task_id` | Task being dispatched |
| `goal` | The task's goal text |
| `attempt` | 1-based attempt number (`retry_count + 1`) |

### `task.progress` — v1
Emitted by: workers (`ctx.emit_progress`), scheduler (retry notices).
Consumers: notifiers (progress pings), memory triggers.

| Field | Meaning |
|-------|---------|
| `task_id` | Task reporting progress |
| `message` | Human-readable progress line |
| (extensible) | Workers may add keys, e.g. `step` |

### `task.needs_human` — v1
Emitted by: the hard-gate enforcer (spec §1); later, planner/recovery
decision points. **Payload must name the specific decision** — never a bare
"stuck, please advise".
Consumers: notifiers (this is the approval request), audit.

| Field | Meaning |
|-------|---------|
| `task_id` | Blocked task |
| `reason` | `"hard_gate"` (later: `"decision"`, `"environment"`, ...) |
| `tool` | Tool name (hard-gate case) |
| `action` | Exact action awaiting approval (e.g. `run exploit/multi/handler`) |
| `target` | Exact target (e.g. `10.0.0.5:4444`) |

For planner decision points (Phase 4+), use `reason: "decision"` and an
`options: [...]` field naming the concrete approaches to pick between.

### `task.done` — v1
Emitted by: scheduler after a worker returns.
Consumers: notifiers, parent-task aggregation, memory compression trigger.

| Field | Meaning |
|-------|---------|
| `task_id` | Completed task |
| `result_summary` | Short summary reported to the parent |
| `artifacts` | List of produced artifact paths |
| `subtasks_spawned` | How many child tasks were created |

### `task.failed` — v1
Emitted by: scheduler, terminal failures only (retries are `task.progress`
— don't spam notifiers with retryable blips).
Consumers: notifiers, recovery analytics.

| Field | Meaning |
|-------|---------|
| `task_id` | Failed task |
| `error` | Reason, including exception type |
| `failure_class` | `transient` / `logic` / `environment` |
| `retry_count` | Retries already burned |
| `terminal` | Always `true` at v1 (non-terminal retries are progress events) |
| `dependents_blocked` | Task ids cascade-blocked by this failure |

### `budget.warning` — v1
Emitted by: cost/quota trackers (Phase 4.5: key-pool exhaustion, circuit
breakers, OpenRouter free-tier near-limit).
Consumers: notifiers.

| Field | Meaning |
|-------|---------|
| `source` | What is warning (e.g. `nim_key_pool`) |
| `detail` | Human-readable specifics |
| `usage` | Optional structured usage numbers |

### `plugin.loaded` — v1
Emitted by: the plugin loader (Phase 6.5), once per plugin load.
Consumers: audit log, notifiers.

| Field | Meaning |
|-------|---------|
| `plugin` | Plugin name |
| `version` | Plugin version |
| `capabilities_assigned` | Capabilities the **kernel** assigned |
| `upgraded` | Which tools had `hard_gate` forced to true over the manifest's claim |

## Replay

Events are durable, so "what actually happened during that run" is a query,
not a log grep:

```python
from kernel.event_bus import EventBus
bus = EventBus("state/agent.db")
for e in bus.replay(since=run_started_at):
    print(e.id, e.type, e.payload)
# or one type only:
failures = bus.replay(event_type="task.failed")
```

## Dead letters

A consumer that fails an event `max_attempts` times (default 5) has the
event moved to `dead_letters` (consumer, event, error, timestamp recorded)
and its offset advanced past it — one poison event can't stall a consumer
forever, and nothing is silently dropped. Today the alert is a `logger.error`
line + the durable row; a notifier consumer for dead letters arrives with
the notify layer (Phase 4.5).
