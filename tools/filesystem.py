"""Filesystem tool (spec §6 order 1). `hard_gate: false` — local file I/O
on the agent's own machine (spec §1.1 names filesystem *read* explicitly;
writes here are local-user-permissions-bound, not actions against external
targets).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from kernel.recovery import EnvironmentFailure, LogicError
from .base import ParamSpec, Tool, ToolResult

ACTIONS = ("read", "write", "append", "list", "exists", "mkdir")


class FilesystemTool(Tool):
    name = "filesystem"
    description = (
        "Read, write, append, list, check existence of, or create "
        "directories on the local filesystem."
    )
    hard_gate = False
    params = [
        ParamSpec("action", str, f"One of {', '.join(ACTIONS)}"),
        ParamSpec("path", str, "Target file or directory path"),
        ParamSpec("content", str, "Content for write/append",
                  required=False, default=""),
    ]

    async def run(self, action: str, path: str, content: str = "") -> ToolResult:
        if action not in ACTIONS:
            return ToolResult(False, error=f"unknown action {action!r}; "
                                           f"expected one of {ACTIONS}",
                              failure_class="logic")
        try:
            return await asyncio.to_thread(self._run_sync, action, path, content)
        except PermissionError as exc:
            return ToolResult(False, error=f"permission denied: {exc}",
                              failure_class="environment")
        except FileNotFoundError as exc:
            return ToolResult(False, error=f"not found: {exc}",
                              failure_class="logic")
        except (LogicError, EnvironmentFailure) as exc:
            return ToolResult(False, error=str(exc),
                              failure_class="logic"
                              if isinstance(exc, LogicError) else "environment")

    def _run_sync(self, action: str, path: str, content: str) -> ToolResult:
        p = Path(path)
        if action == "read":
            text = p.read_text(errors="replace")
            return ToolResult(True, output=text, data={"bytes": len(text)})
        if action == "write":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return ToolResult(True, output=f"wrote {len(content)} chars to {path}",
                              data={"bytes": len(content)})
        if action == "append":
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(content)
            return ToolResult(True, output=f"appended {len(content)} chars to {path}",
                              data={"bytes": len(content)})
        if action == "list":
            if not p.is_dir():
                raise LogicError(f"not a directory: {path}")
            entries = sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir())
            return ToolResult(True, output="\n".join(entries),
                              data={"entries": entries})
        if action == "exists":
            exists = p.exists()
            return ToolResult(True, output=f"{path}: {'exists' if exists else 'missing'}",
                              data={"exists": exists, "is_dir": p.is_dir() if exists else False})
        if action == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            return ToolResult(True, output=f"created {path}")
        raise LogicError(f"unhandled action {action!r}")  # pragma: no cover
