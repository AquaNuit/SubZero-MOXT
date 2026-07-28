"""Tool framework (spec §6). Built so far (Phase 3):

filesystem, shell, git, python_exec, package_manager, docker — all
`hard_gate: false` (local operations on the agent's own machine).

Later phases add: browser (Phase 5), re_static/ghidra_bridge (Phase 5.5,
hard_gate: false), security_active/ (hard_gate: true ONLY, spec §1.4).
"""

from __future__ import annotations

from .base import (
    Completed,
    CommandTimeout,
    ParamSpec,
    Tool,
    ToolExecutor,
    ToolParamError,
    ToolResult,
    async_subprocess_runner,
)
from .registry import ToolRegistry, ToolRegistrationError
from .shell import ShellTool
from .filesystem import FilesystemTool
from .git import GitTool
from .python_exec import PythonExecTool
from .package_managers import DistroInfo, PackageManagerTool, detect_distro
from .docker import DockerTool


def default_registry() -> ToolRegistry:
    """A registry with all built-in Phase 3 tools registered (real runners).

    Tests construct tools individually with injected fakes instead — see
    tests/test_tools_exec.py for the pattern.
    """
    registry = ToolRegistry()
    registry.register(FilesystemTool())
    registry.register(ShellTool())
    registry.register(GitTool())
    registry.register(PythonExecTool())
    registry.register(PackageManagerTool())
    registry.register(DockerTool())
    return registry


__all__ = [
    "Completed",
    "CommandTimeout",
    "ParamSpec",
    "Tool",
    "ToolExecutor",
    "ToolParamError",
    "ToolResult",
    "async_subprocess_runner",
    "ToolRegistry",
    "ToolRegistrationError",
    "ShellTool",
    "FilesystemTool",
    "GitTool",
    "PythonExecTool",
    "DistroInfo",
    "PackageManagerTool",
    "detect_distro",
    "DockerTool",
    "default_registry",
]
