# Tool API

**Status: contract fixed (Phase 1, via the hard-gate enforcer); tool
implementations start in Phase 3.**

## The interface

Every tool — built-in or plugin — exposes:

| Member | Type | Notes |
|--------|------|-------|
| `name` | `str` | Stable, unique (registry key) |
| `description` | `str` | What the planner reads to decide usage |
| `parameters` | typed schema | Typed parameters; no free-form `**kwargs` at the boundary |
| `hard_gate` | `bool` | **spec §1 — see below** |
| `run(...)` | structured result | Raises kernel marker exceptions, never leaks tracebacks into the scheduler |

## `hard_gate` — the non-negotiable part (spec §1)

- Read-only / analysis tools (filesystem read, static
  disassembly/decompilation of a binary already on disk, `git log`,
  package status queries): `hard_gate: false`.
- Tools that act against a live external target (exploit modules, crafted
  traffic at hosts you don't control, credential spraying, that family):
  `hard_gate: true`.
- A `hard_gate: true` call is **never executed by the scheduler**. The
  kernel's `HardGateEnforcer` (built, `kernel/hard_gate_enforcer.py`)
  emits `task.needs_human` naming the exact action + target and blocks
  until an explicit approval arrives through the Command Worker. This holds
  even in full-autonomous mode, and there is no bypass — do not write one.
- Tool authors do not implement gating themselves. They declare
  `hard_gate`, and every call goes through the enforcer:

```python
from kernel.hard_gate_enforcer import HardGateEnforcer, ToolCallSpec

enforcer.enforce(task_id, ToolCallSpec(
    tool_name="exploit_runner",
    action="run exploit/multi/handler",   # concrete — vague is rejected
    target="10.0.0.5:4444",               # concrete — approval binds to it
    hard_gate=True,
))
# returns: approved -> proceed; raises HardGateDenied -> surface via Recovery
```

Approvals bind to the exact `tool|action|target` triple and persist in the
kernel DB (`approvals` table), so a restart neither loses nor re-asks a
decision.

## Error handling

Tools raise the kernel's marker exceptions (`TransientError`, `LogicError`,
`EnvironmentFailure`) with actionable messages; the scheduler's boundary
converts anything else into Recovery input. A tool exception must never
crash the scheduler (tested in `tests/test_recovery.py`).

## Build order (spec §6)

1. **Phase 3:** filesystem, shell, git, Python execution; package managers
   (detect distro first — apt/dnf/pacman/flatpak/snap — and adapt).
   Phase 3 also adds Docker/container management.
2. **Phase 5:** browser automation (Playwright): navigate, click, fill
   forms, screenshot, download, extract structured data.
3. **Phase 5.5:** static RE — Ghidra bridge exposing
   decompile/disassemble/symbol-index as `hard_gate: false` (analysis of a
   binary already on disk touches nothing external).
4. **Active security tooling** (spec §1.4): register with
   `hard_gate: true` and nothing more. Re-read spec §1 before touching this
   family. The Phase 5.5 acceptance test proves a gated stub cannot execute
   without an approval event.

## Registry (Phase 3)

`tools/registry.py` maps name → tool and is the planner's catalog. The
registry enforces the interface at registration time (missing `hard_gate`
or untyped parameters = refused), so a malformed tool fails loudly at load,
not silently mid-task.
