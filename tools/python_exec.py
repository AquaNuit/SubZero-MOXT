"""Python execution tool (spec §6 order 1). `hard_gate: false` — runs code
in a local subprocess with the same interpreter, on the agent's own machine.

This executes arbitrary Python by design: it's the agent's hands on its own
laptop under the user's automation. It is NOT a sandbox. Anything reaching
a live external target belongs in a `hard_gate: true` tool instead (§1).
"""

from __future__ import annotations

import shlex
import sys
from typing import Optional

from .base import (
    CommandTimeout,
    Completed,
    ParamSpec,
    Tool,
    ToolResult,
    async_subprocess_runner,
)


class PythonExecTool(Tool):
    name = "python_exec"
    description = (
        "Execute Python code (or a .py script) in a subprocess and return "
        "its stdout/stderr and exit code."
    )
    hard_gate = False
    params = [
        ParamSpec("code", str, "Python source to execute with -c",
                  required=False, default=""),
        ParamSpec("script_path", str, "Path to a .py file to run instead",
                  required=False, default=""),
        ParamSpec("cwd", str, "Working directory", required=False, default=None),
        ParamSpec("timeout_s", float, "Kill after this many seconds",
                  required=False, default=60.0),
    ]

    def __init__(self, runner=None, interpreter: Optional[str] = None):
        self._runner = runner or async_subprocess_runner
        self.interpreter = interpreter or sys.executable

    async def run(self, code: str = "", script_path: str = "",
                  cwd: Optional[str] = None, timeout_s: float = 60.0) -> ToolResult:
        if bool(code) == bool(script_path):
            return ToolResult(
                False, error="exactly one of code/script_path is required",
                failure_class="logic")
        if code:
            command = f"{shlex.quote(self.interpreter)} -c {shlex.quote(code)}"
        else:
            command = (f"{shlex.quote(self.interpreter)} "
                       f"{shlex.quote(script_path)}")
        try:
            completed: Completed = await self._runner(command, cwd, timeout_s)
        except CommandTimeout as exc:
            return ToolResult(False, error=str(exc), failure_class="transient",
                              data={"timed_out": True})
        ok = completed.returncode == 0
        output = completed.stdout.strip()
        if completed.stderr.strip():
            output += ("\n" if output else "") + "[stderr]\n" + completed.stderr.strip()
        return ToolResult(
            ok, output=output,
            error="" if ok else f"exit code {completed.returncode}",
            failure_class=None if ok else "logic",
            data={"returncode": completed.returncode},
        )
