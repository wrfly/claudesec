"""`claudesec discuss NAME on|off` puts a running worker into discussion mode and back: on, its
turns end waiting for the user in its window with no report (the Stop hook lets them through) and
the user is switched to it; its DONE takes the user back to the secretary. Run by the secretary,
it says how to tell the worker; run by the worker on itself, it doesn't.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_external_sessions import load


class DiscussSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.cs.secretary_alive = lambda: True
        self.cs.alive_windows = lambda: {"@2"}
        self.calls = []
        self.cs.tmux = lambda *a, **k: self.calls.append(a) or ""
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "BLOCKED", "task": "ask me 30 things", "model": "claude-opus-5-5", "window": "@2",
                  "agent": "claude", "mode": "task"}}}))
        self.cs.prepare_worker("w", {"secretary_name": "secretary"}, "hi", self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def discuss(self, state, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out):
            if env is None:  # run by the secretary, or from a worker's subprocess in this test run
                os.environ.pop("CLAUDESEC_WORKER", None)
            self.cs.cmd_discuss(SimpleNamespace(name="w", state=state))
        return out.getvalue()

    def stop(self):
        out, old = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps({"session_id": "s"}))
        try:
            with contextlib.redirect_stdout(out):
                self.cs.hook_worker_stop("w")
        finally:
            sys.stdin = old
        return out.getvalue()

    def test_on_lets_turns_end_unreported_and_off_brings_the_hook_back(self):
        self.assertIn("block", self.stop())
        out = self.discuss("on")
        self.assertEqual(self.cs.read_registry()[1]["w"]["mode"], "discuss")
        self.assertEqual((self.cs.worker_dir("w") / "mode").read_text(), "discuss")
        self.assertIn(("select-window", "-t", "@2"), self.calls)
        self.assertIn("SendMessage(to: 'w')", out)
        self.assertEqual(self.stop(), "")
        self.discuss("off")
        self.assertEqual(self.cs.read_registry()[1]["w"]["mode"], "task")
        self.assertIn("block", self.stop())

    def test_the_worker_switching_itself_is_not_told_to_tell_itself(self):
        out = self.discuss("on", env={"CLAUDESEC_WORKER": "w"})
        self.assertNotIn("SendMessage", out)
        self.assertEqual(self.cs.read_registry()[1]["w"]["mode"], "discuss")

    def test_its_done_takes_the_user_back_to_the_secretary(self):
        self.discuss("on")
        self.calls.clear()
        self.cs.record_report("w", "TYPE: DONE\nDID: asked all 30")
        self.assertTrue(any(c[0] == "select-window" and c[2].endswith(":secretary") for c in self.calls))

    def test_no_state_only_says_the_mode(self):
        self.assertRegex(self.discuss(None), "任务模式|task mode")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
