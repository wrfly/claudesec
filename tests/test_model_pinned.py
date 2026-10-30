"""A model asked for by name (spawn / wake --model) is the one that runs, even after an earlier
usage-credits dialog taught claudesec to swap it; its credits dialog is left up and reported as
STUCK ON A DIALOG, since paying for it is the user's call.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_external_sessions import load
from test_stuck import CREDITS

SWITCH_MODEL = """
   Switch to Fable 5.1?
   Fable 5.1 runs on usage credits, purchased separately from your plan.
   ❯ No, keep my current model
     Continue with Fable 5.1
   Enter to confirm · Esc to cancel
"""


class ModelPinned(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "state"
        self.home.mkdir()
        self.cs = load(self.home)
        self.write({"focus": None, "workers": {}, "credit_models": {"claude-fable-5-1": "claude-opus-5-5"}})
        self.cmds, self.keys, self.screen = [], [], CREDITS
        wdir = Path(self.tmp.name) / "w"
        wdir.mkdir()
        self.cs.prepare_worker = lambda *a, **kw: wdir
        self.cs.has_session = lambda s: False
        self.cs.alive_windows = lambda: {"@9"}
        self.cs.pane_text = lambda target, suggestion="mark": self.screen
        self.cs.tmux = self.fake_tmux
        patcher = mock.patch.object(self.cs.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, reg):
        (self.home / "registry.json").write_text(json.dumps(reg))

    def reg(self):
        return json.loads((self.home / "registry.json").read_text())

    def fake_tmux(self, *args, check=True):
        if args[0] == "new-window":
            self.cmds.append(args[-1])
            return "@9\n"
        if args[0] == "send-keys":
            self.keys.append(args[-1])
        return ""

    def spawn(self, *argv):
        saved_argv, saved_stdin = sys.argv, sys.stdin
        sys.argv, sys.stdin = ["claudesec", "spawn", *argv], io.StringIO("do it")
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        return out.getvalue()

    def test_model_by_name_is_kept_and_its_dialog_reported(self):
        out = self.spawn("modrules", "--dir", self.tmp.name, "--model", "claude-fable-5-1")
        self.assertIn("--model claude-fable-5-1", self.cmds[0])
        w = self.reg()["workers"]["modrules"]
        self.assertEqual(w["model"], "claude-fable-5-1")
        self.assertTrue(w["model_pinned"])
        self.assertIn("STUCK ON A DIALOG", out)
        self.assertIn("usage credits", out)
        self.assertEqual(self.keys, [])  # not answered for the user
        self.assertEqual(self.cs.check_stuck(now_t=1000), [])  # the watchdog leaves it too, then alerts
        self.assertEqual(self.keys, [])
        self.assertEqual(len(self.cs.check_stuck(now_t=1061)), 1)

    def test_model_name_after_the_options_too(self):
        self.spawn("--model", "claude-fable-5-1", "modrules", "--dir", self.tmp.name)
        self.assertEqual(self.reg()["workers"]["modrules"]["model"], "claude-fable-5-1")

    def test_default_model_still_swapped(self):
        (self.home / "config.json").write_text(json.dumps({"worker_model": "claude-fable-5-1"}))
        self.screen = ""
        self.spawn("plain", "--dir", self.tmp.name)
        self.assertIn("--model claude-opus-5-5", self.cmds[0])
        self.assertNotIn("model_pinned", self.reg()["workers"]["plain"])

    def test_model_dialogs_title_is_not_a_switch_option(self):
        self.assertIsNone(self.cs.SWITCH.search("   Switch to Fable 5.1?"))
        self.assertEqual(self.cs.SWITCH.search(" ❯ Switch to Opus 5.5 and continue")[1], "Opus 5.5")
        self.screen = SWITCH_MODEL
        self.assertIsNone(self.cs.answer_credit_dialog("x", "@9"))
        self.assertEqual(self.keys, [])


if __name__ == "__main__":
    unittest.main()
