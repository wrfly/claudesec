"""The secretary's alert watcher across a turn that dies on an API error: the alerts it was
woken for are not lost, and a new watcher is armed, after a pause.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_external_sessions import load


class WatchFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.cs.claude_sessions = lambda: []  # owner: this test process's parent, alive
        self.alerts = self.home / "alerts"
        (self.alerts / "delivered").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, fn, event):
        saved, err = sys.stdin, io.StringIO()
        sys.stdin = io.StringIO(json.dumps({"hook_event_name": event, "session_id": "s"}))
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as e:
                fn()
        finally:
            sys.stdin = saved
        return e.exception.code, err.getvalue()

    def test_failed_turn_requeues_its_alerts_and_rearms_after_a_pause(self):
        (self.alerts / "1.txt").write_text("r14 → latency\n")
        code, out = self.run_hook(self.cs.hook_secretary_watch, "Stop")
        self.assertEqual(code, 2)
        self.assertIn("r14", out)
        self.assertFalse((self.alerts / "1.txt").exists())
        self.assertEqual(json.loads((self.alerts / "inflight.json").read_text()), ["1.txt"])
        # that turn dies on "Credit balance is too low"
        self.cs.WATCH_RETRY = 1
        start = time.monotonic()
        code, out = self.run_hook(self.cs.hook_secretary_watch_failed, "StopFailure")
        self.assertEqual(code, 2)
        self.assertIn("r14", out)  # delivered again
        self.assertGreaterEqual(time.monotonic() - start, 1)  # after the pause

    def test_a_normal_turn_end_forgets_what_it_handled(self):
        (self.alerts / "inflight.json").write_text(json.dumps(["1.txt"]))
        (self.alerts / "delivered" / "1.txt").write_text("old\n")
        (self.alerts / "2.txt").write_text("new\n")
        code, out = self.run_hook(self.cs.hook_secretary_watch, "Stop")
        self.assertEqual(code, 2)
        self.assertIn("\nnew", out)
        self.assertNotIn("old", out)
        self.assertEqual(json.loads((self.alerts / "inflight.json").read_text()), ["2.txt"])


if __name__ == "__main__":
    unittest.main()
