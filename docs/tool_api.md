# Tool API

**Status: BUILT (Phase 3, 2026-07-28)** — registry, executor, and the six
Phase 3 tools. Browser arrives Phase 5; Ghidra bridge Phase 5.5; active
security tooling is `hard_gate: true` only, always (spec §1.4).

## The interface (`tools/base.py`)

Every tool — built-in or plugin — subclasses `Tool` and declares:

| Member | Type | Notes |
|--------|------|-------|
| `name` | `str` | Stable, unique (registry key) |
| `description` | `str` | What the planner reads to decide usage |
| `params` | `list[ParamSpec]` | Typed params (str/int/float/bool) with required/default; coerced + validated before every call |
| `hard_gate` | `bool` | **Explicit bool, required — spec §1** |
| `run(**params)` | `async -> ToolResult` | Never raises for expected failures |

`ToolResult`: `ok`, `output`, `error`, `failure_class`
(`transient`/`logic`/`environment`/`None` — feeds Recovery §2.3), `data`.
`raise_for_failure()` converts a failed result into the kernel's marker
exception for workers that want scheduler Recovery to engage.

## Registry (`tools/registry.py`)

Registration-time enforcement: missing `hard_gate` (must be a real bool,
not None), missing/duplicate name, empty description, or malformed params =
**refused loudly at load**, not silently broken mid-task. `catalog()`
renders the planner-facing view of every tool.

## Executor — the single call path

`ToolExecutor(registry, enforcer).execute(tool_name, *, task_id,
gate_action, gate_target, **params)`:

1. **validate** — params type-checked (errors → `logic` result)
2. **gate** — `hard_gate: true` tools ONLY: routes through the kernel
   `HardGateEnforcer` with the concrete `gate_action`/`gate_target`
   (vague = rejected as `logic`; denied = structured result
   `data={"gate": "denied"}`; **no enforcer configured = refused, never
   executed**). Ungated tools skip this entirely.
3. **run** — exceptions become structured results (marker exceptions keep
   their class; unknowns → `transient`); output capped at 8000 chars.

Nothing raises out of the executor — the scheduler boundary stays clean.

`gate_action`/`gate_target` are executor-level kwargs so tool parameters
may freely be named `action`/`target` (filesystem, git, and
package_manager all use `action`).

## Built-in tools (Phase 3)

All `hard_gate: false` — local operations on the agent's own machine, not
actions against live external targets (§1 taxonomy):

| Tool | Actions / params | Notes |
|------|------------------|-------|
| `filesystem` | read, write, append, list, exists, mkdir + path/content | Auto-creates parents on write |
| `shell` | command, cwd, timeout_s | Process-group kill on timeout (`transient`) |
| `git` | init, status, log, diff, add, commit, current_branch | Optional commit-author override |
| `python_exec` | code XOR script_path, cwd, timeout_s | Same interpreter, subprocess isolation |
| `package_manager` | install, remove, search, is_installed, update_index + dry_run | **Distro-adaptive**: apt/dnf/pacman/flatpak/snap via /etc/os-release (ID → ID_LIKE → binary probe) |
| `docker` | ps, images, pull, run, stop, logs, remove | Daemon-down → structured `environment` failure |

**Dry-run** (spec §8): `package_manager` with `dry_run: true` returns the
exact command it *would* run and executes nothing — verified by test.

**Runner injection**: every command-executing tool takes a `runner`
(`async (command, cwd, timeout_s) -> Completed`) so tests assert command
construction and flows without real subprocesses; the default is
`async_subprocess_runner` (process-group kill on timeout).

## Gated tools (Phase 5.5+)

When active security tooling lands: register with `hard_gate: true`, call
through the executor with concrete `gate_action`/`gate_target`. The
enforcer emits `task.needs_human`, blocks, and the call proceeds only on
explicit approval bound to the exact `tool|action|target` (already built
and tested — `kernel/hard_gate_enforcer.py`, `tests/test_hard_gate.py`,
executor wiring in `tests/test_tools_registry.py`). There is no bypass —
do not write one.

## Acceptance (spec §10, Phase 3) — passes

`tests/test_phase3_acceptance.py`: a scheduler-run task installs a package
through **two detected-distro code paths** (apt via Debian os-release, dnf
via Fedora — production command adaptation asserted verbatim), writes and
really executes a diagnostic script, commits it to a real git repo, and
reports through the event bus. Task ends `done` with evidence in
`result_summary`.
