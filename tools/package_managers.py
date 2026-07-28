"""Package manager tool (spec §6 order 2): detect the distro first, adapt
commands accordingly — apt / dnf / pacman / flatpak / snap.

`hard_gate: false`: package operations are local system administration on
the agent's own machine (spec §1.1 names package *status queries* read-only;
install/remove are local-admin actions the user asked the agent to perform,
not actions against external targets). Permission modes + audit log (§8,
Phase 4.5) layer on top.

- Distro detection parses /etc/os-release (ID, then ID_LIKE) and falls back
  to probing for flatpak/snap binaries. Fully injectable for hermetic tests.
- Dry-run support (spec §8): every action can report the exact command it
  WOULD run without executing anything.
"""

from __future__ import annotations

import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .base import (
    CommandTimeout,
    Completed,
    ParamSpec,
    Tool,
    ToolResult,
    async_subprocess_runner,
)

ID_TO_MANAGER = {
    "debian": "apt", "ubuntu": "apt", "linuxmint": "apt", "raspbian": "apt",
    "pop": "apt", "kali": "apt",
    "fedora": "dnf", "rhel": "dnf", "centos": "dnf", "rocky": "dnf",
    "almalinux": "dnf",
    "arch": "pacman", "manjaro": "pacman", "endeavouros": "pacman",
}

ACTIONS = ("install", "remove", "search", "is_installed", "update_index")


@dataclass
class DistroInfo:
    distro_id: str
    id_like: list[str] = field(default_factory=list)
    manager: str = "unknown"  # apt | dnf | pacman | flatpak | snap | unknown


def parse_os_release(text: str) -> tuple[str, list[str]]:
    distro_id, id_like = "", []
    for line in text.splitlines():
        if line.startswith("ID="):
            distro_id = line[3:].strip().strip('"').lower()
        elif line.startswith("ID_LIKE="):
            id_like = line[8:].strip().strip('"').lower().split()
    return distro_id, id_like


def detect_distro(
    os_release_text: Optional[str] = None,
    command_exists: Optional[Callable[[str], bool]] = None,
) -> DistroInfo:
    """Detect the package manager. Injectable for hermetic tests.

    Resolution order: exact ID -> ID_LIKE -> flatpak binary -> snap binary.
    """
    if os_release_text is None:
        try:
            os_release_text = Path("/etc/os-release").read_text()
        except OSError:
            os_release_text = ""
    exists = command_exists or (lambda cmd: shutil.which(cmd) is not None)
    distro_id, id_like = parse_os_release(os_release_text)

    for candidate in [distro_id, *id_like]:
        if candidate in ID_TO_MANAGER:
            return DistroInfo(distro_id, id_like, ID_TO_MANAGER[candidate])
    if exists("flatpak"):
        return DistroInfo(distro_id, id_like, "flatpak")
    if exists("snap"):
        return DistroInfo(distro_id, id_like, "snap")
    return DistroInfo(distro_id, id_like, "unknown")


def build_command(manager: str, action: str, package: str = "") -> str:
    """The exact command for (manager, action). Pure — dry-run returns this
    verbatim, and tests assert on it without executing anything."""
    pkg = shlex.quote(package) if package else ""
    needs_pkg = action in ("install", "remove", "search", "is_installed")
    if needs_pkg and not pkg:
        raise ValueError(f"action {action!r} requires a package name")

    table = {
        "apt": {
            "install": f"sudo apt-get install -y {pkg}",
            "remove": f"sudo apt-get remove -y {pkg}",
            "search": f"apt-cache search {pkg}",
            "is_installed": f"dpkg -s {pkg}",
            "update_index": "sudo apt-get update",
        },
        "dnf": {
            "install": f"sudo dnf install -y {pkg}",
            "remove": f"sudo dnf remove -y {pkg}",
            "search": f"dnf search {pkg}",
            "is_installed": f"rpm -q {pkg}",
            "update_index": "sudo dnf makecache",
        },
        "pacman": {
            "install": f"sudo pacman -S --noconfirm {pkg}",
            "remove": f"sudo pacman -R --noconfirm {pkg}",
            "search": f"pacman -Ss {pkg}",
            "is_installed": f"pacman -Q {pkg}",
            "update_index": "sudo pacman -Sy",
        },
        "flatpak": {
            "install": f"flatpak install -y flathub {pkg}",
            "remove": f"flatpak uninstall -y {pkg}",
            "search": f"flatpak search {pkg}",
            "is_installed": f"flatpak info {pkg}",
        },
        "snap": {
            "install": f"sudo snap install {pkg}",
            "remove": f"sudo snap remove {pkg}",
            "search": f"snap find {pkg}",
            "is_installed": f"snap list {pkg}",
            "update_index": "sudo snap refresh",
        },
    }
    if manager not in table:
        raise ValueError(f"no command mapping for manager {manager!r}")
    if action not in table[manager]:
        raise ValueError(f"action {action!r} not supported for {manager}")
    return table[manager][action]


class PackageManagerTool(Tool):
    name = "package_manager"
    description = (
        "Install, remove, search, or check packages on the local machine. "
        "Detects the distro (apt/dnf/pacman/flatpak/snap) and adapts. "
        "Supports dry_run to preview the exact command."
    )
    hard_gate = False
    params = [
        ParamSpec("action", str, f"One of {', '.join(ACTIONS)}"),
        ParamSpec("package", str, "Package name", required=False, default=""),
        ParamSpec("dry_run", bool, "Report the command without executing",
                  required=False, default=False),
        ParamSpec("timeout_s", float, "Command timeout", required=False,
                  default=300.0),
    ]

    def __init__(self, runner=None, distro: Optional[DistroInfo] = None,
                 os_release_text: Optional[str] = None,
                 command_exists: Optional[Callable[[str], bool]] = None):
        self._runner = runner or async_subprocess_runner
        # Distro is resolved lazily and cached; injectables keep it hermetic.
        self._distro = distro
        self._os_release_text = os_release_text
        self._command_exists = command_exists

    @property
    def distro(self) -> DistroInfo:
        if self._distro is None:
            self._distro = detect_distro(self._os_release_text,
                                         self._command_exists)
        return self._distro

    async def run(self, action: str, package: str = "", dry_run: bool = False,
                  timeout_s: float = 300.0) -> ToolResult:
        if action not in ACTIONS:
            return ToolResult(False, error=f"unknown action {action!r}; "
                                           f"expected one of {ACTIONS}",
                              failure_class="logic")
        distro = self.distro
        if distro.manager == "unknown":
            return ToolResult(
                False,
                error=f"no supported package manager found (distro "
                      f"{distro.distro_id or 'unidentified'})",
                failure_class="environment")
        try:
            command = build_command(distro.manager, action, package)
        except ValueError as exc:
            return ToolResult(False, error=str(exc), failure_class="logic")

        if dry_run:
            return ToolResult(True, output=f"DRY RUN: {command}",
                              data={"command": command, "dry_run": True,
                                    "manager": distro.manager})

        try:
            completed: Completed = await self._runner(command, None, timeout_s)
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
            # A failed install is usually environmental (mirror, network,
            # permissions); a failed is_installed just means "not there".
            failure_class=None if ok else (
                "logic" if action == "is_installed" else "environment"),
            data={"returncode": completed.returncode, "command": command,
                  "manager": distro.manager},
        )
