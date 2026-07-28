# Roadmap

Phases and acceptance tests per spec §10. Rule: implement in order, and a
phase's acceptance test must pass before the next phase starts.

| Phase | Deliverable | Acceptance test | Status |
|-------|-------------|-----------------|--------|
| 1 | Kernel: task graph + scheduler + event bus (versioned, durable) + provider interface (local Ollama only) | Kill/restart mid-task → resume with no re-prompt; event survives a bus restart | **DONE (35/35 tests)** |
| 2 | Memory/context system + workspace indexer | Run a task long enough to force a compression pass; active context stays bounded; indexer updates only the changed file's subgraph | Not started |
| 3 | Linux tools + execution engine | Agent installs a package, runs a script, reports result, on 2+ distros | Not started |
| 4 | Coding agent workflow (plan/edit/test/debug loop) | Agent fixes a seeded bug in a small repo end-to-end unattended, using the workspace indexer instead of a fresh full-repo scan | Not started |
| 4.5 | Notifier/Command layer + NIM key pool + remaining providers + Recovery Manager failure classification | Pull the network cable on the main NIM key mid-task → pool fails over; approve/deny a `needs_human` from Telegram; force transient vs logic failure → different recovery paths | Not started |
| 5 | Browser automation | Agent completes a multi-step web task (navigate, fill form, screenshot) unattended | Not started |
| 5.5 | Static RE tooling (Ghidra bridge), hard-gate config | A `hard_gate: true` stub tool cannot execute without an approval event, by test (enforcer already exists — this wires it to tools) | Not started |
| 6 | Multi-agent orchestration (subagent spawning/merging) | Two subagents complete independent subtasks of one root task and merge results | Not started |
| 6.5 | Plugin SDK | Load a plugin that under-declares capabilities; kernel overrides `hard_gate` correctly and logs the override | Not started |
| 7 | Performance polish | Steady-state VRAM/RAM stay under budget during a long-running task | Not started |

## Notes on sequencing decisions

- **Recovery upgrade sits in 4.5, not Phase 1.** Replan-on-logic-failure
  needs the planner (Phase 4) to exist first; `needs_human` escalation
  needs the notifier. Phase 1 ships classification + transient policy so
  the structure is in place.
- **The hard-gate enforcer was built in Phase 1** even though its formal
  acceptance test is listed under 5.5: it is spec §1 (non-negotiable) and
  belongs to the kernel. `tests/test_hard_gate.py` already proves the gate;
  Phase 5.5 adds the tool-registry wiring test.
- **`config/routing.yaml` is written** (spec §4) but unread until the
  router lands in Phase 4.5 — keeping the table as config from day one
  avoids hardcoded routing creeping into Phase 2–4 code.
- **Phase 2 next.** The workspace indexer and compression pass are
  prerequisites for the Phase 4 coding workflow's acceptance test
  (indexer-first code navigation), so nothing should jump the queue.
