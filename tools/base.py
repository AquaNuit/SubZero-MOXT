"""Tool interface + executor (spec §6, contract in docs/tool_api.md).

Every tool exposes: name, description, typed parameters, `hard_gate: bool`
(spec §1), and structured error handling. Tool exceptions never crash the
scheduler: the executor converts everything into a `ToolResult` whose
`failure_class` carries the kernel's recovery classification, so a worker
can decide to retry/replan without the scheduler's boundary ever seeing an
unhandled exception.

The gated-call path (spec §1) is wired HERE, once, for all tools: the
executor routes every `hard_gate: true` call through the kernel's
HardGateEnforcer before the tool's `run` is invoked. Tools never implement
gating themselves — they declare `hard_gate` and the kernel decides.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from kernel.hard_gate_enforcer import HardGateDenied, HardGateEnforcer, ToolCallSpec
from kernel.recovery import EnvironmentFailure, LogicError, TransientError


class ToolParamError(LogicError):
    """Bad/missing/mistyped parameters — the planner's fault (logic)."""


@dataclass
class ParamSpec:
    name: str
    type: type  # str | int | float | bool
    description: str = ""
    required: bool = True
    default: Any = None

    def coerce(self, value: Any) -> Any:
        if self.type is bool:
            if isinstance(value, bool):
                return value
            raise ToolParamError(
                f"param {self.name!r} expects bool, got {type(value).__name__}")
        if self.type is int:
            if isinstance(value, bool):  # bool is an int subclass — reject
                raise ToolParamError(f"param {self.name!r} expects int, got bool")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ToolParamError(
                    f"param {self.name!r} expects int: {exc}") from exc
        if self.type is float:
            if isinstance(value, bool):
                raise ToolParamError(f"param {self.name!r} expects float, got bool")
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ToolParamError(
                    f"param {self.name!r} expects float: {exc}") from exc
        if self.type is str:
            if not isinstance(value, str):
                raise ToolParamError(
                    f"param {self.name!r} expects str, got {type(value).__name__}")
            return value
        raise ToolParamError(f"param {self.name!r} has unsupported type {self.type}")


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    # "transient" | "logic" | "environment" | None — feeds Recovery (§2.3).
    failure_class: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)

    def raise_for_failure(self) -> None:
        """Convert a failed result into the kernel's marker exception —
        for workers that want the scheduler's Recovery Manager to engage."""
        if self.ok:
            return
        message = self.error or self.output or "tool call failed"
        if self.failure_class == "transient":
            raise TransientError(message)
        if self.failure_class == "environment":
            raise EnvironmentFailure(message)
        raise LogicError(message)


class Tool:
    """The interface every tool — built-in or plugin — implements.

    Class attributes `name`, `description`, `hard_gate`, `params` are
    validated by the registry at registration time (missing/invalid =
    refused loudly, not silently broken mid-task).
    """

    name: str = ""
    description: str = ""
    hard_gate: Optional[bool] = None  # MUST be overridden with a real bool
    params: list[ParamSpec] = []

    def validate(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Type-check and complete parameters. Raises ToolParamError."""
        spec_by_name = {p.name: p for p in self.params}
        unknown = set(raw) - set(spec_by_name)
        if unknown:
            raise ToolParamError(
                f"unknown param(s) for {self.name}: {sorted(unknown)}")
        cleaned: dict[str, Any] = {}
        for spec in self.params:
            if spec.name in raw:
                cleaned[spec.name] = spec.coerce(raw[spec.name])
            elif spec.required:
                raise ToolParamError(
                    f"missing required param {spec.name!r} for {self.name}")
            else:
                cleaned[spec.name] = spec.default
        return cleaned

    async def run(self, **kwargs) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError


_MARKER_TO_CLASS = {
    TransientError: "transient",
    LogicError: "logic",
    EnvironmentFailure: "environment",
}


def failure_class_of(exc: BaseException) -> str:
    for cls, name in _MARKER_TO_CLASS.items():
        if isinstance(exc, cls):
            return name
    return "transient"  # unknowns treated as transient (kernel convention)


class ToolExecutor:
    """The single call path for all tools (spec §6).

    validate -> (gate if hard_gate) -> run -> structured result.
    Every kernel->service boundary rule applies: nothing raises out of here
    except nothing. Ever. (Denials and failures come back as ToolResult.)
    """

    OUTPUT_CAP = 8000

    def __init__(self, registry, enforcer: Optional[HardGateEnforcer] = None):
        self.registry = registry
        self.enforcer = enforcer

    async def execute(
        self,
        tool_name: str,
        *,
        task_id: str = "",
        gate_action: str = "",
        gate_target: str = "",
        **params,
    ) -> ToolResult:
        """Run a tool by name.

        `gate_action`/`gate_target` are the concrete description presented
        at the hard gate (required for hard_gate:true tools only — spec §1);
        all other kwargs pass through to the tool unchanged, so tool params
        may freely be named `action` or `target`.
        """
        tool = self.registry.get(tool_name)
        if tool is None:
            return ToolResult(False, error=f"unknown tool {tool_name!r}",
                              failure_class="logic")
        try:
            cleaned = tool.validate(params)
        except ToolParamError as exc:
            return ToolResult(False, error=str(exc), failure_class="logic")

        if tool.hard_gate:
            # spec §1: gated tools NEVER run without an explicit recorded
            # approval. An executor without an enforcer must refuse, not run.
            if self.enforcer is None:
                return ToolResult(
                    False,
                    error="hard-gated tool called without a HardGateEnforcer "
                          "— refusing to execute (spec §1)",
                    failure_class="environment")
            try:
                self.enforcer.enforce(task_id, ToolCallSpec(
                    tool_name=tool.name, action=gate_action,
                    target=gate_target, hard_gate=True, args=cleaned))
            except HardGateDenied as exc:
                return ToolResult(False, error=str(exc),
                                  data={"gate": "denied"})
            except ValueError as exc:  # vague action/target rejected
                return ToolResult(False, error=str(exc), failure_class="logic")

        try:
            result = await tool.run(**cleaned)
        except (TransientError, LogicError, EnvironmentFailure) as exc:
            return ToolResult(False, error=str(exc),
                              failure_class=failure_class_of(exc))
        except Exception as exc:  # noqa: BLE001 — boundary, by design
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}",
                              failure_class="transient")
        if len(result.output) > self.OUTPUT_CAP:
            result.output = (result.output[: self.OUTPUT_CAP]
                             + f"\n...[truncated at {self.OUTPUT_CAP} chars]")
        return result


# ------------------------------------------------------------ shell runner
# Shared by shell/git/python_exec/package/docker tools. Injectable in each
# tool's constructor so tests stay hermetic (no real subprocesses needed
# unless the test chooses the real runner).

@dataclass
class Completed:
    returncode: int
    stdout: str
    stderr: str


class CommandTimeout(TransientError):
    """Command exceeded its timeout; process group was killed."""


async def async_subprocess_runner(
    command: str,
    cwd: Optional[str] = None,
    timeout_s: float = 60.0,
) -> Completed:
    """Run a shell command, capture output, kill the process group on
    timeout. `start_new_session` puts the command in its own process group
    so a timeout kills the whole tree, not just the shell."""
    import os
    import signal

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        await proc.wait()
        raise CommandTimeout(
            f"command timed out after {timeout_s}s: {command[:120]}") from exc
    return Completed(
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode(errors="replace"),
        stderr_b.decode(errors="replace"),
    )
