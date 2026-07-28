"""Subprocess fixture for the kill/restart resume acceptance test.

Usage: python3 tests/_resume_runner.py <db_path> <work_file>

Simulates a long-running multi-step task. The worker is idempotent: each
completed step is appended to <work_file>, and on re-dispatch (after a
kill) already-completed steps are skipped. First launch creates the root
task; later launches find it in the persisted graph and resume — no user
re-prompting.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.event_bus import EventBus
from kernel.scheduler import Scheduler, WorkerResult
from kernel.task_graph import TaskGraph

STEPS = 6
STEP_DELAY_S = 0.4


async def worker(task, ctx):
    work_file = os.environ["RESUME_WORK_FILE"]
    done = set()
    if os.path.exists(work_file):
        with open(work_file) as f:
            done = set(f.read().split())
    for i in range(STEPS):
        step = f"step-{i}"
        if step in done:
            continue
        await asyncio.sleep(STEP_DELAY_S)
        with open(work_file, "a") as f:
            f.write(step + "\n")
        ctx.emit_progress(f"completed {step}")
    return WorkerResult(
        result_summary=f"{STEPS} steps completed",
        artifacts=[work_file],
    )


def main() -> int:
    db_path, work_file = sys.argv[1], sys.argv[2]
    os.environ["RESUME_WORK_FILE"] = work_file

    graph = TaskGraph(db_path)
    bus = EventBus(db_path)
    if not graph.all_tasks():
        graph.add_task(goal="demo-long-task")
        print("TASK_CREATED", flush=True)
    else:
        print("TASK_RESUMED", flush=True)

    scheduler = Scheduler(graph, bus, worker, poll_interval_s=0.1)
    asyncio.run(scheduler.run_until_idle(timeout_s=30))

    task = graph.all_tasks()[0]
    print(f"ALL_DONE status={task.status}", flush=True)
    return 0 if task.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
