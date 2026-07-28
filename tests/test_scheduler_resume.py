"""Phase 1 acceptance test (spec §10):

    Kill the process mid-task, restart it, and the Scheduler resumes from
    the persisted graph with no user re-prompting required.

Also verifies, on the real killed-run database:
    - the event log survived the kill (durable bus, §2.2)
    - exactly one task exists afterwards (no duplicate root task was
      created on restart — resume, not restart-from-scratch)
    - the idempotent worker produced each step exactly once.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "tests" / "_resume_runner.py"


def _wait_for(predicate, timeout_s, interval=0.05):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class SchedulerResumeTest(unittest.TestCase):
    def test_kill_mid_task_restart_resumes_without_reprompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "agent.db")
            work_file = str(Path(tmp) / "progress.txt")
            env = dict(os.environ, PYTHONUNBUFFERED="1")

            # --- First run: killed mid-task -------------------------------
            proc_a = subprocess.Popen(
                [sys.executable, str(RUNNER), db, work_file],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            def two_steps_done():
                return os.path.exists(work_file) and len(
                    Path(work_file).read_text().split()
                ) >= 2

            started = _wait_for(two_steps_done, timeout_s=15)
            self.assertTrue(started, "worker never started writing progress")
            proc_a.kill()
            proc_a.communicate(timeout=10)
            self.assertNotEqual(proc_a.returncode, 0)

            steps_at_kill = Path(work_file).read_text().split()
            self.assertLess(len(steps_at_kill), 6, "task finished before kill?!")

            # --- Restart: must resume, not re-prompt ----------------------
            proc_b = subprocess.run(
                [sys.executable, str(RUNNER), db, work_file],
                capture_output=True, text=True, timeout=60, env=env,
            )
            self.assertEqual(proc_b.returncode, 0, proc_b.stderr)
            self.assertIn("TASK_RESUMED", proc_b.stdout)
            self.assertNotIn("TASK_CREATED", proc_b.stdout)
            self.assertIn("ALL_DONE status=done", proc_b.stdout)

            # --- Every step ran exactly once (idempotent resume) ----------
            steps = Path(work_file).read_text().split()
            self.assertEqual(sorted(steps), [f"step-{i}" for i in range(6)])
            self.assertEqual(len(steps), len(set(steps)))

            # --- Persisted graph: one task, done, with summary ------------
            conn = sqlite3.connect(db)
            rows = conn.execute(
                "SELECT status, result_summary, retry_count FROM tasks"
            ).fetchall()
            self.assertEqual(len(rows), 1, "restart must not duplicate the task")
            self.assertEqual(rows[0][0], "done")
            self.assertEqual(rows[0][1], "6 steps completed")

            # --- Durable events survived the kill -------------------------
            events = conn.execute(
                "SELECT type, payload FROM events ORDER BY id"
            ).fetchall()
            conn.close()
            types = [t for t, _ in events]
            self.assertGreaterEqual(types.count("task.started"), 2,
                                    "task must be dispatched twice (kill+resume)")
            self.assertEqual(types.count("task.done"), 1)
            self.assertIn("task.progress", types)


if __name__ == "__main__":
    unittest.main()
