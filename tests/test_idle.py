"""The secretary idle with something for the user is woken to ask it: after idle_ask_minutes,
each nudge waiting longer, none once they are used up; the user speaking or new items start them
over; never while busy, not running, or "don't disturb me".

    python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_external_sessions import load


class Idle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "secretary.session").write_text("sec\n")
        (self.home / "queue.md").write_text(self.cs.queue_template())
        self.session = {"sessionId": "sec", "status": "idle", "statusUpdatedAt": time.time() * 1000}
        self.cs.claude_sessions = lambda: [self.session]
        self.alerts = self.home / "alerts"

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, text, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_queue(argparse.Namespace(action="add", words=[text], asked=False, later=False, idea=False, **kw))

    def idle_for(self, minutes):
        self.session.update(status="idle", statusUpdatedAt=(time.time() - minutes * 60) * 1000)

    def nudges(self):
        """Alerts raised since the last look, taken away as the watcher would."""
        files = sorted(self.alerts.glob("*.txt")) if self.alerts.exists() else []
        out = [f.read_text() for f in files]
        for f in files:
            f.unlink()
        return out

    def check(self, minutes):
        self.idle_for(minutes)
        self.cs.check_idle()
        return self.nudges()

    def user_says(self):
        saved = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"prompt": "12 A"}))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.cs.hook_secretary_prompt()
        finally:
            sys.stdin = saved

    def test_nudges_once_idle_long_enough_then_backs_off_and_stops(self):
        self.add("[w] 现在开 PR 吗？")
        self.assertEqual(self.check(4), [])
        first = self.check(5)
        self.assertEqual(len(first), 1)
        self.assertIn("[闲置]", first[0])
        self.assertIn("#1", first[0])
        self.assertEqual(self.check(6), [])    # the same batch: not again at 5
        self.assertEqual(self.check(14), [])
        self.assertEqual(len(self.check(15)), 1)
        last = self.check(30)
        self.assertEqual(len(last), 1)
        self.assertIn("不再叫你", last[0])
        self.assertEqual(self.check(300), [])  # used up

    def test_the_user_speaking_or_a_new_item_starts_the_waits_over(self):
        self.add("[w] 一")
        self.assertEqual(len(self.check(5)), 1)
        self.user_says()
        self.assertEqual(len(self.check(5)), 1)
        self.assertEqual(self.check(5), [])
        self.add("[w] 二")
        self.assertEqual(len(self.check(5)), 1)

    def test_to_dos_count_but_put_off_items_dont(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_queue(argparse.Namespace(action="add", words=["[w] 以后再说"], asked=False, later=True, idea=False))
        self.assertEqual(self.check(60), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_notice(argparse.Namespace(action="add", words=["去 X 窗口说 push"], source="w"))
        self.assertEqual(len(self.check(60)), 1)

    def test_not_while_busy_not_running_dnd_or_off(self):
        self.add("[w] 一")
        self.session["status"] = "busy"
        self.cs.check_idle()
        self.assertEqual(self.nudges(), [])
        self.cs.claude_sessions = lambda: []
        self.assertEqual(self.check(60), [])
        self.cs.claude_sessions = lambda: [self.session]
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_dnd(argparse.Namespace(state="on"))
        self.assertEqual(self.check(60), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_dnd(argparse.Namespace(state="off"))
        self.cs.config = lambda: {**self.cs.DEFAULT_CONFIG, "idle_ask_minutes": []}
        self.assertEqual(self.check(60), [])


if __name__ == "__main__":
    unittest.main()
