"""Docker / container management tool (spec §6 order 3).

`hard_gate: false` — local container administration on the agent's own
machine. The runner is injectable: tests assert command construction and
output handling without a daemon, and the daemon-unavailable path is a
structured `environment` failure, not a crash.
"""

from __future__ import annotations

import shlex
from typing import Optional

from .base import (
    CommandTimeout,
    Completed,
    ParamSpec,
    Tool,
    ToolResult,
    async_subprocess_runner,
)

ACTIONS = ("ps", "images", "pull", "run", "stop", "logs", "remove")


class DockerTool(Tool):
    name = "docker"
    description = (
        "Manage local Docker containers: ps, images, pull, run, stop, "
        "logs, remove. Reports daemon unavailability as an environment "
        "failure."
    )
    hard_gate = False
    params = [
        ParamSpec("action", str, f"One of {', '.join(ACTIONS)}"),
        ParamSpec("image", str, "Image name (pull/run)", required=False, default=""),
        ParamSpec("name", str, "Container name (run/stop/logs/remove)",
                  required=False, default=""),
        ParamSpec("command", str, "Command to run inside (run only)",
                  required=False, default=""),
        ParamSpec("timeout_s", float, "Command timeout", required=False,
                  default=120.0),
    ]

    def __init__(self, runner=None):
        self._runner = runner or async_subprocess_runner

    def _build(self, action: str, image: str, name: str, command: str) -> str:
        if action == "ps":
            return "docker ps -a"
        if action == "images":
            return "docker images"
        if action == "pull":
            if not image:
                raise ValueError("pull requires an image")
            return f"docker pull {shlex.quote(image)}"
        if action == "run":
            if not image:
                raise ValueError("run requires an image")
            name_part = f" --name {shlex.quote(name)}" if name else ""
            cmd_part = f" {command}" if command else ""
            return f"docker run -d{name_part} {shlex.quote(image)}{cmd_part}"
        if action in ("stop", "logs", "remove"):
            if not name:
                raise ValueError(f"{action} requires a container name")
            verb = {"stop": "stop", "logs": "logs", "remove": "rm -f"}[action]
            return f"docker {verb} {shlex.quote(name)}"
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")

    async def _check_daemon(self) -> Optional[ToolResult]:
        try:
            completed: Completed = await self._runner("docker info", None, 15.0)
        except CommandTimeout:
            return ToolResult(False, error="docker daemon not responding",
                              failure_class="environment")
        except FileNotFoundError:
            return ToolResult(False, error="docker binary not installed",
                              failure_class="environment")
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "docker info failed"
            return ToolResult(False, error=f"docker daemon unavailable: {detail}",
                              failure_class="environment")
        return None

    async def run(self, action: str, image: str = "", name: str = "",
                  command: str = "", timeout_s: float = 120.0) -> ToolResult:
        try:
            cmd = self._build(action, image, name, command)
        except ValueError as exc:
            return ToolResult(False, error=str(exc), failure_class="logic")
        daemon_error = await self._check_daemon()
        if daemon_error is not None:
            return daemon_error
        try:
            completed: Completed = await self._runner(cmd, None, timeout_s)
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
            failure_class=None if ok else "environment",
            data={"returncode": completed.returncode, "command": cmd},
        )
