"""Real (hermetic) execution-tool tests: shell, filesystem, git, python_exec.

These use the REAL runners against tmp dirs — no fakes — because the whole
point of Phase 3 is that the agent can actually operate the machine.
"""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.base import Completed
from tools.filesystem import FilesystemTool
from tools.git import GitTool
from tools.python_exec import PythonExecTool
from tools.shell import ShellTool


def run(coro):
    return asyncio.run(coro)


class ShellToolTest(unittest.TestCase):
    def test_echo_real(self):
        result = run(ShellTool().run("echo hello-subzero"))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "hello-subzero")
        self.assertEqual(result.data["returncode"], 0)

    def test_failing_command(self):
        result = run(ShellTool().run("exit 3"))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["returncode"], 3)
        self.assertEqual(result.failure_class, "logic")

    def test_timeout_kills_process(self):
        result = run(ShellTool().run("sleep 30", timeout_s=0.3))
        self.assertFalse(result.ok)
        self.assertTrue(result.data.get("timed_out"))
        self.assertEqual(result.failure_class, "transient")

    def test_cwd_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(ShellTool().run("pwd && echo oops >&2", cwd=tmp))
            self.assertTrue(result.ok)
            self.assertIn(tmp, result.output)
            self.assertIn("[stderr]", result.output)

    def test_injected_runner(self):
        async def fake(command, cwd, timeout_s):
            return Completed(0, f"fake ran: {command}", "")

        result = run(ShellTool(runner=fake).run("anything"))
        self.assertTrue(result.ok)
        self.assertIn("fake ran: anything", result.output)


class FilesystemToolTest(unittest.TestCase):
    def test_full_cycle(self):
        fs = FilesystemTool()
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "sub" / "note.txt")
            r = run(fs.run("write", target, "line1\n"))
            self.assertTrue(r.ok)
            r = run(fs.run("append", target, "line2\n"))
            self.assertTrue(r.ok)
            r = run(fs.run("read", target))
            self.assertEqual(r.output, "line1\nline2\n")
            r = run(fs.run("exists", target))
            self.assertTrue(r.data["exists"])
            r = run(fs.run("list", str(Path(tmp) / "sub")))
            self.assertEqual(r.data["entries"], ["note.txt"])
            r = run(fs.run("mkdir", str(Path(tmp) / "made")))
            self.assertTrue(r.ok)
            self.assertTrue((Path(tmp) / "made").is_dir())

    def test_read_missing_is_logic(self):
        result = run(FilesystemTool().run("read", "/no/such/file.txt"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "logic")

    def test_unknown_action_is_logic(self):
        result = run(FilesystemTool().run("delete", "/tmp/x"))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "logic")


class PythonExecToolTest(unittest.TestCase):
    def test_real_execution(self):
        result = run(PythonExecTool().run(code="print(6 * 7)"))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "42")

    def test_exception_propagates_as_result(self):
        result = run(PythonExecTool().run(code="raise ValueError('boom')"))
        self.assertFalse(result.ok)
        self.assertIn("ValueError", result.output)

    def test_timeout(self):
        result = run(PythonExecTool().run(
            code="import time; time.sleep(30)", timeout_s=0.3))
        self.assertFalse(result.ok)
        self.assertTrue(result.data.get("timed_out"))

    def test_script_path_and_exactly_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "s.py"
            script.write_text("print('from-script')")
            result = run(PythonExecTool().run(script_path=str(script)))
            self.assertEqual(result.output, "from-script")
        bad = run(PythonExecTool().run(code="pass", script_path="/x.py"))
        self.assertFalse(bad.ok)
        self.assertEqual(bad.failure_class, "logic")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitToolTest(unittest.TestCase):
    def test_real_repo_flow(self):
        git = GitTool(commit_author="SubZero Test <test@subzero.local>")
        shell = ShellTool()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(run(git.run("init", tmp)).ok)
            (Path(tmp) / "hello.py").write_text("print('tracked')\n")

            r = run(git.run("status", tmp))
            self.assertTrue(r.ok)
            self.assertIn("hello.py", r.output)

            self.assertTrue(run(git.run("add", tmp, paths="hello.py")).ok)
            r = run(git.run("commit", tmp, message="add hello.py"))
            self.assertTrue(r.ok, r.output)

            r = run(git.run("log", tmp))
            self.assertIn("add hello.py", r.output)

            r = run(git.run("current_branch", tmp))
            self.assertTrue(r.ok)
            self.assertIn(r.output.strip(), ("main", "master"))

    def test_commit_requires_message(self):
        result = run(GitTool().run("commit", "/tmp", message=""))
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_class, "logic")

    def test_command_construction_with_fake_runner(self):
        seen = []

        async def fake(command, cwd, timeout_s):
            seen.append(command)
            return Completed(0, "", "")

        git = GitTool(runner=fake)
        run(git.run("log", "/repo dir", n=5))
        self.assertEqual(seen[0], "git -C '/repo dir' log --oneline -n 5")


if __name__ == "__main__":
    unittest.main()
