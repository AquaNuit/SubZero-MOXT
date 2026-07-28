"""Docker tool tests (spec §6 order 3) — hermetic via injected runner."""

import asyncio
import unittest

from tools.base import Completed
from tools.docker import DockerTool


def run(coro):
    return asyncio.run(coro)


class FakeDockerRunner:
    """daemon_ok controls the `docker info` probe; records the rest."""

    def __init__(self, daemon_ok=True):
        self.daemon_ok = daemon_ok
        self.commands = []

    async def __call__(self, command, cwd, timeout_s):
        if command == "docker info":
            return Completed(0 if self.daemon_ok else 1,
                             "", "" if self.daemon_ok else "Cannot connect")
        self.commands.append(command)
        return Completed(0, "ok-output", "")


class DockerToolTest(unittest.TestCase):
    def test_daemon_unavailable_is_environment_failure(self):
        tool = DockerTool(runner=FakeDockerRunner(daemon_ok=False))
        result = run(tool.run("ps"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "environment")
        self.assertIn("unavailable", result.error)

    def test_ps_and_images(self):
        runner = FakeDockerRunner()
        tool = DockerTool(runner=runner)
        self.assertTrue(run(tool.run("ps")).ok)
        self.assertTrue(run(tool.run("images")).ok)
        self.assertEqual(runner.commands, ["docker ps -a", "docker images"])

    def test_run_command_construction(self):
        runner = FakeDockerRunner()
        tool = DockerTool(runner=runner)
        result = run(tool.run("run", image="ubuntu:24.04", name="sandbox1",
                              command="sleep infinity"))
        self.assertTrue(result.ok)
        self.assertEqual(
            runner.commands[0],
            "docker run -d --name sandbox1 ubuntu:24.04 sleep infinity")

    def test_pull_and_stop_and_logs(self):
        runner = FakeDockerRunner()
        tool = DockerTool(runner=runner)
        run(tool.run("pull", image="alpine"))
        run(tool.run("stop", name="sandbox1"))
        run(tool.run("logs", name="sandbox1"))
        self.assertEqual(runner.commands, [
            "docker pull alpine",
            "docker stop sandbox1",
            "docker logs sandbox1",
        ])

    def test_missing_params_are_logic_failures(self):
        tool = DockerTool(runner=FakeDockerRunner())
        self.assertEqual(run(tool.run("run")).failure_class, "logic")
        self.assertEqual(run(tool.run("stop")).failure_class, "logic")
        self.assertEqual(run(tool.run("teleport")).failure_class, "logic")


if __name__ == "__main__":
    unittest.main()
