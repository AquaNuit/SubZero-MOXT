"""Git tool (spec §6 order 1). Local repository operations —
`hard_gate: false` (no external target; `git log` is named read-only in
spec §1.1, and local add/commit/status are the agent's own workspace).

Commit identity: uses the repo/global git config by default; pass
`commit_author="Name <email>"` to override (tests and unattended runs).
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

ACTIONS = ("init", "status", "log", "diff", "add", "commit", "current_branch")


class GitTool(Tool):
    name = "git"
    description = (
        "Operate on a local git repository: init, status, log, diff, add, "
        "commit, current_branch."
    )
    hard_gate = False
    params = [
        ParamSpec("action", str, f"One of {', '.join(ACTIONS)}"),
        ParamSpec("repo", str, "Path to the repository"),
        ParamSpec("paths", str, "Space-separated paths for add (default: -A)",
                  required=False, default=""),
        ParamSpec("message", str, "Commit message (required for commit)",
                  required=False, default=""),
        ParamSpec("n", int, "Max log entries", required=False, default=10),
    ]

    def __init__(self, runner=None, commit_author: Optional[str] = None):
        self._runner = runner or async_subprocess_runner
        self.commit_author = commit_author

    def _build(self, action: str, repo: str, paths: str, message: str,
               n: int) -> str:
        q_repo = shlex.quote(repo)
        if action == "init":
            return f"git -C {q_repo} init"
        if action == "status":
            return f"git -C {q_repo} status --porcelain=v1 --branch"
        if action == "log":
            return f"git -C {q_repo} log --oneline -n {n}"
        if action == "diff":
            return f"git -C {q_repo} diff HEAD"
        if action == "add":
            target = paths.strip() if paths.strip() else "-A"
            return f"git -C {q_repo} add {target}"
        if action == "commit":
            if not message.strip():
                raise ValueError("commit requires a non-empty message")
            ident = ""
            if self.commit_author:
                name, _, email = self.commit_author.partition("<")
                ident = (f"-c user.name={shlex.quote(name.strip())} "
                         f"-c user.email={shlex.quote(email.rstrip('>').strip())} ")
            return f"git {ident}-C {q_repo} commit -m {shlex.quote(message)}"
        if action == "current_branch":
            return f"git -C {q_repo} rev-parse --abbrev-ref HEAD"
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")

    async def run(self, action: str, repo: str, paths: str = "",
                  message: str = "", n: int = 10) -> ToolResult:
        try:
            command = self._build(action, repo, paths, message, n)
        except ValueError as exc:
            return ToolResult(False, error=str(exc), failure_class="logic")
        try:
            completed: Completed = await self._runner(command, None, 60.0)
        except CommandTimeout as exc:
            return ToolResult(False, error=str(exc), failure_class="transient")
        ok = completed.returncode == 0
        output = completed.stdout.strip()
        if completed.stderr.strip():
            output += ("\n" if output else "") + "[stderr]\n" + completed.stderr.strip()
        return ToolResult(
            ok, output=output,
            error="" if ok else f"git exited {completed.returncode}",
            failure_class=None if ok else "logic",
            data={"returncode": completed.returncode, "command": command},
        )
