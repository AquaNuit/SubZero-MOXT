"""Shell tool (spec §6 order 1).

Runs shell commands on the agent's own machine — `hard_gate: false` (local
execution, not an action against a live external target; see docs/tool_api.md).
The runner is injectable so tests stay hermetic.
"""

from __future__ import annotations

from typing import Optional

from .base import (
    CommandTimeout,
    Completed,
    ParamSpec,
    Tool,
    ToolResult,
    async_subprocess_runner,
)


class ShellTool(Tool):
    name = "shell"
    description = (
        "Run a shell command on the local machine and return its output. "
        "Use for system administration, scripting, and CLI programs."
    )
    hard_gate = False
    params = [
        ParamSpec("command", str, "The shell command to execute"),
        ParamSpec("cwd", str, "Working directory", required=False, default=None),
        ParamSpec("timeout_s", float, "Kill the command after this many seconds",
                  required=False, default=60.0),
    ]

    def __init__(self, runner=None):
        self._runner = runner or async_subprocess_runner

    async def run(self, command: str, cwd: Optional[str] = None,
                  timeout_s: float = 60.0) -> ToolResult:
        try:
            completed: Completed = await self._runner(command, cwd, timeout_s)
        except CommandTimeout as exc:
            return ToolResult(False, error=str(exc), failure_class="transient",
                              data={"timed_out": True})
        output = completed.stdout
        if completed.stderr:
            output += ("\n" if output else "") + "[stderr]\n" + completed.stderr
        return ToolResult(
            completed.returncode == 0,
            output=output.strip(),
            error="" if completed.returncode == 0
            else f"exit code {completed.returncode}",
            failure_class=None if completed.returncode == 0 else "logic",
            data={"returncode": completed.returncode},
        )
