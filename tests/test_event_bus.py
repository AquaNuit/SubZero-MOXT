"""Event bus tests: durability, at-least-once, dead-letter, replay (§2.2).

Includes the Phase 1 acceptance property: an event survives a bus restart.
"""

import tempfile
import unittest
from pathlib import Path

from kernel.event_bus import EventBus


class EventBusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "agent.db")
        self.bus = EventBus(self.db)

    def tearDown(self):
        self.bus.close()
        self._tmp.cleanup()

    def test_publish_assigns_schema_version(self):
        eid = self.bus.publish("task.started", {"task_id": "t1"})
        event = self.bus.replay()[0]
        self.assertEqual(event.id, eid)
        self.assertEqual(event.schema_version, 1)
        self.assertEqual(event.payload["task_id"], "t1")

    def test_fan_out_per_consumer(self):
        self.bus.publish("task.done", {"task_id": "t1"})
        self.assertEqual(len(self.bus.pending("telegram")), 1)
        self.assertEqual(len(self.bus.pending("discord")), 1)
        self.bus.ack("telegram", self.bus.replay()[0].id)
        self.assertEqual(len(self.bus.pending("telegram")), 0)
        self.assertEqual(len(self.bus.pending("discord")), 1)

    def test_event_survives_bus_restart_unacked(self):
        """Acceptance: publish, 'restart' the bus, unacked event redelivers."""
        self.bus.publish("task.failed", {"task_id": "tX"})
        self.bus.close()
        bus2 = EventBus(self.db)  # new instance, same DB = process restart
        pending = bus2.pending("telegram")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].payload["task_id"], "tX")
        bus2.close()

    def test_ack_survives_restart_no_redelivery(self):
        self.bus.publish("task.done", {"task_id": "t1"})
        eid = self.bus.pending("telegram")[0].id
        self.bus.ack("telegram", eid)
        self.bus.close()
        bus2 = EventBus(self.db)
        self.assertEqual(bus2.pending("telegram"), [])
        bus2.close()

    def test_dispatch_retries_failed_handler(self):
        self.bus.publish("task.progress", {"n": 1})
        calls = []

        def flaky(event):
            calls.append(event.id)
            if len(calls) < 3:
                raise RuntimeError("telegram api unreachable")

        self.assertEqual(self.bus.dispatch("telegram", flaky), 0)
        self.assertEqual(self.bus.dispatch("telegram", flaky), 0)
        self.assertEqual(self.bus.dispatch("telegram", flaky), 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.bus.dead_letters(), [])

    def test_dead_letter_after_max_attempts(self):
        self.bus.publish("task.progress", {"n": 1})
        self.bus.publish("task.progress", {"n": 2})

        def always_fails(event):
            raise RuntimeError("permanent")

        for _ in range(3):
            self.bus.dispatch("telegram", always_fails, max_attempts=3)
        dls = self.bus.dead_letters()
        self.assertEqual(len(dls), 2)
        self.assertEqual(dls[0].consumer, "telegram")
        self.assertIn("permanent", dls[0].error)
        # Poison events no longer block the consumer.
        self.assertEqual(self.bus.pending("telegram"), [])

    def test_replay_since(self):
        import time
        self.bus.publish("task.started", {"task_id": "a"})
        midpoint = time.time()
        time.sleep(0.01)
        self.bus.publish("task.done", {"task_id": "a"})
        recent = self.bus.replay(since=midpoint)
        self.assertEqual([e.type for e in recent], ["task.done"])
        by_type = self.bus.replay(event_type="task.started")
        self.assertEqual(len(by_type), 1)


if __name__ == "__main__":
    unittest.main()
