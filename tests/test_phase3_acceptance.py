"""Phase 3 acceptance test (spec §10):

    Agent can install a package, run a script, report result, on 2+ distros.

Sandbox interpretation (justified): the sandbox has one real distro and no
root, so the install step is driven through TWO detected-distro code paths
(apt via Debian os-release, dnf via Fedora os-release) with recording fake
runners — the exact production code paths, including command adaptation —
while script write/run/git/report steps run fully real through the Phase 1
Scheduler. On a real multi-distro deployment the same worker runs unchanged
with the real runner.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from kernel.event_bus import EventBus
from kernel.scheduler import Scheduler, WorkerResult
from kernel.task_graph import TaskGraph
from tools.base import Completed, ToolExecutor
from tools.filesystem import FilesystemTool
from tools.git import GitTool
from tools.package_managers import PackageManagerTool
from tools.python_exec import PythonExecTool
from tools.registry import ToolRegistry

DEBIAN = 'ID=debian\n'
FEDORA = 'ID=fedora\n'

SCRIPT = """import platform
print("report: uname=" + platform.system())
print("report: computation=" + str(sum(range(10))))
"""


class Phase3AcceptanceTest(unittest.TestCase):
    def test_install_run_report_two_distros_via_scheduler(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "agent.db")
            workdir = Path(tmp) / "work"
            workdir.mkdir()

            graph = TaskGraph(db)
            bus = EventBus(db)
            graph.add_task("install htop, run diagnostic script, report")

            # --- Registry: real tools + package tools bound to two distro
            # code paths (apt + dnf) with recording runners. ---------------
            apt_runner, dnf_runner = _recorder(), _recorder()
            registry = ToolRegistry()
            registry.register(FilesystemTool())
            registry.register(PythonExecTool())
            registry.register(GitTool(
                commit_author="SubZero Agent <agent@subzero.local>"))
            registry.register(PackageManagerTool(
                runner=apt_runner, os_release_text=DEBIAN))
            # Second package tool instance for the dnf path — same tool
            # class, different detected distro (as on two machines).
            dnf_pkg = PackageManagerTool(runner=dnf_runner,
                                         os_release_text=FEDORA)
            executor = ToolExecutor(registry)

            async def worker(task, ctx):
                evidence = []

                # 1. Install a package on the apt code path...
                r = await executor.execute(
                    "package_manager", action="install", package="htop")
                r.raise_for_failure()
                evidence.append(f"apt: {r.data['command']} -> ok")
                ctx.emit_progress("installed via apt path")

                # 2. ...and on the dnf code path (2nd distro).
                r = await dnf_pkg.run("install", package="htop")
                r.raise_for_failure()
                evidence.append(f"dnf: {r.data['command']} -> ok")
                ctx.emit_progress("installed via dnf path")

                # 3. Write + run a real diagnostic script.
                script = str(workdir / "diagnose.py")
                r = await executor.execute(
                    "filesystem", action="write", path=script, content=SCRIPT)
                r.raise_for_failure()
                r = await executor.execute("python_exec", script_path=script)
                r.raise_for_failure()
                evidence.append("script: " + r.output.replace("\n", " | "))
                ctx.emit_progress("script executed")

                # 4. Commit the artifact to a real git repo.
                await executor.execute("git", action="init", repo=str(workdir))
                r = await executor.execute(
                    "git", action="add", repo=str(workdir), paths="diagnose.py")
                r.raise_for_failure()
                r = await executor.execute(
                    "git", action="commit", repo=str(workdir),
                    message="add diagnostic script")
                r.raise_for_failure()
                evidence.append("git: committed diagnose.py")

                return WorkerResult(
                    result_summary=" || ".join(evidence),
                    artifacts=[script])

            scheduler = Scheduler(graph, bus, worker, poll_interval_s=0.05)
            asyncio.run(scheduler.run_until_idle(timeout_s=60))

            # --- Acceptance assertions ------------------------------------
            task = graph.all_tasks()[0]
            self.assertEqual(task.status, "done", task.result_summary)

            # Install ran on 2+ distro code paths with adapted commands.
            self.assertEqual(apt_runner.commands,
                             ["sudo apt-get install -y htop"])
            self.assertEqual(dnf_runner.commands,
                             ["sudo dnf install -y htop"])

            # Script actually ran and reported.
            summary = task.result_summary
            self.assertIn("report: uname=Linux", summary)
            self.assertIn("report: computation=45", summary)
            self.assertIn("git: committed diagnose.py", summary)

            # Real artifacts exist: script on disk, commit in the repo.
            self.assertTrue((workdir / "diagnose.py").exists())
            log = asyncio.run(GitTool().run("log", str(workdir)))
            self.assertIn("add diagnostic script", log.output)

            # Progress was reported through the event bus.
            progress = [e.payload["message"]
                        for e in bus.replay(event_type="task.progress")]
            self.assertIn("installed via apt path", progress)
            self.assertIn("installed via dnf path", progress)
            self.assertIn("script executed", progress)

            graph.close()
            bus.close()


class _recorder:
    def __init__(self):
        self.commands = []

    async def __call__(self, command, cwd, timeout_s):
        self.commands.append(command)
        return Completed(0, "1 package installed", "")


if __name__ == "__main__":
    unittest.main()
