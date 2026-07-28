"""Package manager tests (spec §6 order 2): distro detection, command
adaptation, dry-run — hermetic for 5 managers + one real-path check on the
sandbox's own distro.
"""

import asyncio
import shutil
import unittest

from tools.base import Completed
from tools.package_managers import (
    DistroInfo,
    PackageManagerTool,
    build_command,
    detect_distro,
    parse_os_release,
)

DEBIAN = 'PRETTY_NAME="Debian GNU/Linux 12"\nID=debian\n'
UBUNTU = 'ID=ubuntu\nID_LIKE=debian\n'
FEDORA = 'ID=fedora\n'
ARCH = 'ID=arch\n'
NOBARA = 'ID=nobara\nID_LIKE="fedora rhel"\n'
EXOTIC = 'ID=someexoticos\n'


def run(coro):
    return asyncio.run(coro)


class DetectionTest(unittest.TestCase):
    def test_parse_os_release(self):
        self.assertEqual(parse_os_release(DEBIAN), ("debian", []))
        self.assertEqual(parse_os_release(NOBARA), ("nobara", ["fedora", "rhel"]))

    def test_direct_id_mapping(self):
        self.assertEqual(detect_distro(DEBIAN).manager, "apt")
        self.assertEqual(detect_distro(FEDORA).manager, "dnf")
        self.assertEqual(detect_distro(ARCH).manager, "pacman")

    def test_id_like_fallback(self):
        self.assertEqual(detect_distro(UBUNTU).manager, "apt")
        self.assertEqual(detect_distro(NOBARA).manager, "dnf")

    def test_flatpak_then_snap_fallback(self):
        info = detect_distro(EXOTIC, command_exists=lambda c: c == "flatpak")
        self.assertEqual(info.manager, "flatpak")
        info = detect_distro(EXOTIC, command_exists=lambda c: c == "snap")
        self.assertEqual(info.manager, "snap")
        info = detect_distro(EXOTIC, command_exists=lambda c: False)
        self.assertEqual(info.manager, "unknown")


class CommandBuilderTest(unittest.TestCase):
    def test_apt_commands(self):
        self.assertEqual(build_command("apt", "install", "htop"),
                         "sudo apt-get install -y htop")
        self.assertEqual(build_command("apt", "is_installed", "htop"),
                         "dpkg -s htop")
        self.assertEqual(build_command("apt", "update_index"),
                         "sudo apt-get update")

    def test_dnf_and_pacman_commands(self):
        self.assertEqual(build_command("dnf", "install", "htop"),
                         "sudo dnf install -y htop")
        self.assertEqual(build_command("dnf", "is_installed", "htop"),
                         "rpm -q htop")
        self.assertEqual(build_command("pacman", "install", "htop"),
                         "sudo pacman -S --noconfirm htop")
        self.assertEqual(build_command("pacman", "search", "htop"),
                         "pacman -Ss htop")

    def test_flatpak_and_snap_commands(self):
        self.assertEqual(build_command("flatpak", "install", "org.app.X"),
                         "flatpak install -y flathub org.app.X")
        self.assertEqual(build_command("snap", "remove", "lxd"),
                         "sudo snap remove lxd")

    def test_package_quoting(self):
        self.assertIn("'weird pkg'", build_command("apt", "install", "weird pkg"))

    def test_errors(self):
        with self.assertRaises(ValueError):
            build_command("apt", "install")  # no package
        with self.assertRaises(ValueError):
            build_command("flatpak", "update_index")  # unsupported action
        with self.assertRaises(ValueError):
            build_command("unknown", "install", "x")


class _RecordingRunner:
    """Fake runner: records commands, returns scripted results."""

    def __init__(self, returncode=0, stdout="done", stderr=""):
        self.commands = []
        self._result = Completed(returncode, stdout, stderr)

    async def __call__(self, command, cwd, timeout_s):
        self.commands.append(command)
        return self._result


class PackageToolFlowTest(unittest.TestCase):
    def test_install_flow_apt(self):
        runner = _RecordingRunner()
        tool = PackageManagerTool(runner=runner, os_release_text=DEBIAN)
        result = run(tool.run("install", package="htop"))
        self.assertTrue(result.ok)
        self.assertEqual(runner.commands, ["sudo apt-get install -y htop"])
        self.assertEqual(result.data["manager"], "apt")

    def test_install_flow_dnf(self):
        runner = _RecordingRunner()
        tool = PackageManagerTool(runner=runner, os_release_text=FEDORA)
        result = run(tool.run("install", package="htop"))
        self.assertTrue(result.ok)
        self.assertEqual(runner.commands, ["sudo dnf install -y htop"])

    def test_dry_run_executes_nothing(self):
        runner = _RecordingRunner()
        tool = PackageManagerTool(runner=runner, os_release_text=DEBIAN)
        result = run(tool.run("install", package="htop", dry_run=True))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "DRY RUN: sudo apt-get install -y htop")
        self.assertEqual(runner.commands, [])  # nothing executed

    def test_failed_install_is_environment(self):
        runner = _RecordingRunner(returncode=1, stderr="mirror unreachable")
        tool = PackageManagerTool(runner=runner, os_release_text=DEBIAN)
        result = run(tool.run("install", package="htop"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "environment")

    def test_is_installed_failure_is_logic_not_error(self):
        runner = _RecordingRunner(returncode=1)
        tool = PackageManagerTool(runner=runner, os_release_text=DEBIAN)
        result = run(tool.run("is_installed", package="nosuchpkg"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "logic")

    def test_unknown_distro_is_environment(self):
        tool = PackageManagerTool(os_release_text=EXOTIC,
                                  command_exists=lambda c: False)
        result = run(tool.run("install", package="htop"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "environment")

    @unittest.skipUnless(shutil.which("dpkg"), "not a dpkg system")
    def test_real_is_installed_on_this_machine(self):
        """Real path, no fakes: bash is installed on any Debian-ish image."""
        result = run(PackageManagerTool().run("is_installed", package="bash"))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.data["manager"], "apt")


if __name__ == "__main__":
    unittest.main()
