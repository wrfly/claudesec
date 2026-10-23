"""A worker whose last report said DONE / FAILED / BLOCKED / NEED_DECISION shows as working again
once a later turn starts, until it reports again.

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class BackAtWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.cs.secretary_alive = lambda: False
        self.cs.tmux = lambda *a, **k: ""
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "task": "t", "dir": "/tmp", "last_summary": ""}}}))

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        w = self.cs.read_registry()[1]["w"]
        return w["status"], w["last_summary"], w.get("pending_ask")

    def report(self, typ, extra=""):
        self.cs.record_report("w", f"TYPE: {typ}\nDID: did it\n{extra}")

    def test_a_later_turn_puts_a_blocked_worker_back_to_work(self):
        self.report("NEED_DECISION", "ASK: A or B?")
        self.cs.back_at_work("w")  # a tool call in the reporting turn itself
        self.assertEqual(self.status()[0], "NEED_DECISION")
        (self.cs.worker_dir("w") / self.cs.AFTER_REPORT).write_text("idle")  # the turn ended
        self.cs.back_at_work("w")  # first tool call of the next turn
        status, summary, ask = self.status()
        self.assertEqual((status, ask), ("PROGRESS", None))
        self.assertTrue(summary.startswith("收到回复，在处理"), summary)
        self.report("BLOCKED", "ISSUE: x")  # and the next report counts again
        self.assertEqual(self.status()[0], "BLOCKED")

    def test_done_then_a_new_task_shows_as_working(self):
        self.report("DONE", "RESULT: r")
        self.cs.back_at_work("w", new_turn=True)  # a Cursor worker's next prompt
        status, summary, _ = self.status()
        self.assertEqual(status, "PROGRESS")
        self.assertIn("上次 DONE", summary)
        self.assertFalse((self.cs.worker_dir("w") / self.cs.AFTER_REPORT).exists())

    def test_progress_reports_leave_nothing_to_undo(self):
        self.report("PROGRESS", "NEXT: n")
        self.assertFalse((self.cs.worker_dir("w") / self.cs.AFTER_REPORT).exists())
        self.cs.back_at_work("w", new_turn=True)
        self.assertEqual(self.status()[0], "PROGRESS")


if __name__ == "__main__":
    unittest.main()
