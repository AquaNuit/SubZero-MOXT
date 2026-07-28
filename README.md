# SubZero

A local-first autonomous AI agent framework in Python. SubZero runs a
long-lived agent that owns a persistent task graph: it plans and executes
multi-step work (coding, Linux administration, static binary analysis,
browser automation), routes model calls across multiple LLM providers, and
reports status over Telegram/Discord instead of blocking on a terminal.

Target hardware is a Linux laptop with an RTX 4050 (6GB VRAM), 16GB RAM,
and a quad-core CPU. Every design decision is checked against that budget:
zero runtime dependencies in Phase 1, a single SQLite file for all kernel
state, one asyncio process, and a default local model that fits in 6GB of
VRAM.

## The non-negotiable constraint (spec §1)

Every tool in SubZero carries a `hard_gate: bool` flag.

- **Read-only / analysis tools** are `hard_gate: false` and run freely.
- **Tools that act against live external targets** are `hard_gate: true`.
  A `hard_gate: true` call is **never** executed by the scheduler. It emits
  a `task.needs_human` event naming the exact action and target, and blocks
  until an explicit human approval arrives via the Command Worker
  (Telegram/Discord).

There is no mode, flag, or "trusted user" bypass, and none may ever be
added. Full-autonomous mode governs everything *except* `hard_gate: true`
tools. This is implemented in `kernel/hard_gate_enforcer.py` and tested in
`tests/test_hard_gate.py`.

## Architecture in one diagram

```
                        +-------------------------------------------+
                        |                 KERNEL                    |
                        |  (small, trusted, owns all durability)    |
                        |                                           |
                        |  Task Graph (SQLite, WAL)   Scheduler     |
                        |  Event Bus (durable outbox) Recovery Mgr  |
                        |  Hard-Gate Enforcer (spec §1)             |
                        +------+--------+--------+--------+---------+
                               |        |        |        |
              timeout + exception boundary on every kernel->service call
                               |        |        |        |
                        +------v--+ +---v-----+ +v-------+ +v---------+
                        |Providers| | Tools   | | Memory | | Notifier |
                        | ollama  | | fs/shell| | (Ph 2) | | TG/Disc  |
                        | nim (45)| | git ... | |        | | (Ph 4.5) |
                        +---------+ +---------+ +--------+ +----------+
                               |
                        +------v------+
                        |   Plugins   |  (Phase 6.5, kernel-enforced
                        |  (Phase 6.5)|   capability gate, no self-
                        +-------------+   declared hard_gate:false)
```

The kernel is the only trusted component. Providers, tools, memory
strategies, notifiers, and plugins are replaceable **services** the kernel
dispatches to. Every kernel-to-service call passes through a timeout +
exception boundary, so a service crash degrades one capability and can never
kill the scheduler loop. See [docs/architecture.md](docs/architecture.md).

## Repository layout

The spec (§9) shows the project tree rooted at `agent-framework/`. This
repository **is** that tree: the GitHub repo root contains `kernel/`,
`providers/`, `tools/`, etc. directly, so imports stay plain
(`from kernel.scheduler import Scheduler`) with no nested package prefix.

```
kernel/        Trusted core: task graph, event bus, recovery, scheduler,
               hard-gate enforcer, shared SQLite helper
providers/     LLM provider interface + registry; LocalOllamaProvider
config/        routing.yaml — provider routing table (read by the Phase 4.5 router)
tools/         Tool packages (empty placeholders until Phase 3)
memory/        Memory system (Phase 2)
notify/        Telegram/Discord notifier + Command Worker (Phase 4.5)
permission/    Permission modes (Phase 4.5; cannot bypass hard gates)
plugins/       Plugin SDK + loaded plugin drop-in directory (Phase 6.5)
tests/         Stdlib unittest suite (35 tests, zero dependencies)
docs/          Project documentation (see below)
state/         Runtime state (SQLite DB lives here; gitignored)
```

## Quickstart

Requirements: **Python 3.11+**. Nothing else — Phase 1 has zero runtime
dependencies (stdlib only).

```bash
# Run the full test suite (121 tests)
python3 -m unittest discover -s tests -t . -v
```

The acceptance tests include killing a subprocess with `kill -9` mid-task
and verifying the restarted scheduler resumes from the persisted task graph
with no user re-prompting (`tests/test_scheduler_resume.py`), and that an
unacknowledged event survives an event-bus restart (`tests/test_event_bus.py`).

To use the local provider at runtime, install
[Ollama](https://ollama.com) and pull the default model
(`ollama pull qwen2.5-coder:7b`, about 4.7GB at q4 — fits the 6GB VRAM
budget). No test requires a live Ollama daemon.

## Current status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Kernel + provider interface | **Done** — 35/35 tests pass |
| 2 | Memory / context + workspace indexer | **Done** — 33/33 tests pass (68 total) |
| 3 | Linux tools + execution engine | **Done** — 53/53 tests pass (121 total) |
| 4 | Coding agent workflow | Planned |
| 4.5 | Notifier/Command layer, NIM key pool, remaining providers, recovery upgrade | Planned |
| 5 | Browser automation | Planned |
| 5.5 | Ghidra bridge (static RE) | Planned |
| 6 | Multi-agent orchestration | Planned |
| 6.5 | Plugin SDK with kernel-side capability enforcement | Planned |
| 7 | Performance polish | Planned |

Details: [docs/implementation_status.md](docs/implementation_status.md),
[docs/roadmap.md](docs/roadmap.md), [docs/changelog.md](docs/changelog.md).

## Documentation

- [docs/architecture.md](docs/architecture.md) — kernel/services split and the durability story
- [docs/module_index.md](docs/module_index.md) — file-by-file index of the codebase
- [docs/implementation_status.md](docs/implementation_status.md) — what is built and tested today
- [docs/roadmap.md](docs/roadmap.md) — the full phase plan with acceptance tests
- [docs/provider_architecture.md](docs/provider_architecture.md) — provider ABC, error mapping, routing
- [docs/tool_api.md](docs/tool_api.md) — tool contract and the hard gate
- [docs/plugin_api.md](docs/plugin_api.md) — plugin manifest and the kernel trust boundary (Phase 6.5)
- [docs/event_schema.md](docs/event_schema.md) — event types, payloads, versioning rules
- [docs/context_management.md](docs/context_management.md) — context pressure and compression (Phase 2)
- [docs/memory_system.md](docs/memory_system.md) — retrieval and structured memory (Phase 2)
- [docs/testing.md](docs/testing.md) — how to run and extend the test suite
- [docs/coding_guidelines.md](docs/coding_guidelines.md) — dependency discipline and conventions
- [docs/performance.md](docs/performance.md) — hardware budget and measurement plan
- [docs/optimization_log.md](docs/optimization_log.md) — log of measured optimizations
- [docs/known_issues.md](docs/known_issues.md) — honest list of current limitations
- docs/agent_handoff.md — handoff notes for agents picking up the next phase (written by the lead engineer)
