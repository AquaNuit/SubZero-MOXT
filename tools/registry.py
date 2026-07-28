"""Tool registry (spec §6): the planner's catalog.

Registration-time enforcement: a tool missing `hard_gate`, a name, a
description, or with malformed parameters is REFUSED at registration —
loudly, at load, not silently mid-task. This is also where the plugin
loader (Phase 6.5) will register plugin tools after the kernel has
re-classified their capabilities (docs/plugin_api.md).
"""

from __future__ import annotations

from typing import Optional

from .base import ParamSpec, Tool


class ToolRegistrationError(ValueError):
    pass


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        problems = self._validate(tool)
        if problems:
            raise ToolRegistrationError(
                f"refusing to register {type(tool).__name__}: "
                + "; ".join(problems))
        self._tools[tool.name] = tool

    def _validate(self, tool: Tool) -> list[str]:
        problems: list[str] = []
        if not isinstance(tool.name, str) or not tool.name.strip():
            problems.append("name must be a non-empty string")
        elif tool.name in self._tools:
            problems.append(f"duplicate tool name {tool.name!r}")
        if not isinstance(tool.description, str) or not tool.description.strip():
            problems.append("description must be non-empty (planners read it)")
        # spec §1: hard_gate must be an explicit bool — never None/missing.
        if not isinstance(tool.hard_gate, bool):
            problems.append(
                "hard_gate must be an explicit bool (spec §1) — "
                "True for tools acting on live external targets, "
                "False for read-only/local analysis")
        if not isinstance(tool.params, list):
            problems.append("params must be a list of ParamSpec")
        else:
            seen: set[str] = set()
            for p in tool.params:
                if not isinstance(p, ParamSpec):
                    problems.append(f"param {p!r} is not a ParamSpec")
                    continue
                if p.name in seen:
                    problems.append(f"duplicate param name {p.name!r}")
                seen.add(p.name)
        return problems

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def catalog(self) -> list[dict]:
        """What the planner sees: names, descriptions, params, gate flags."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "hard_gate": t.hard_gate,
                "params": [
                    {
                        "name": p.name,
                        "type": p.type.__name__,
                        "required": p.required,
                        "description": p.description,
                        **({"default": p.default} if not p.required else {}),
                    }
                    for p in t.params
                ],
            }
            for t in self.list()
        ]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
