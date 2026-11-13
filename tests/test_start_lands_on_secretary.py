"""`claudesec` with the secretary already running brings the user to the secretary's window,
also when run from another window of the claudesec session (2026-11-13: switch-client to the
session the client was already in moved nothing).

    python3 -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_external_sessions import load


class StartLandsOnSecretary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"tmux_session": "claudesec"}))
        self.calls = []
        self.cs.tmux = self.fake_tmux
        self.cs.has_session = lambda name: True
        self.cs.secretary_pane = lambda sess: "%286"
        self.cs.secretary_runs_claudesec = lambda sess, pane: True
        self.cs.arrange_dashboard = lambda sess: None
        self.cs.extended_keys = lambda: None

    def tearDown(self):
        self.tmp.cleanup()

    def fake_tmux(self, *args, check=True):
        self.calls.append(args)
        return ""

    def start(self, **kw):
        args = SimpleNamespace(**{"new": False, "restart": False, "detach": False, **kw})
        self.cs.cmd_start(args)

    def test_inside_tmux_selects_the_secretary_window_then_switches(self):
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1/default,1,0"}):
            self.start()
        names = [c[0] for c in self.calls]
        self.assertIn(("select-window", "-t", "%286"), self.calls)
        self.assertIn(("select-pane", "-t", "%286"), self.calls)
        self.assertLess(names.index("select-window"), names.index("switch-client"))
        self.assertNotIn("respawn-pane", names)  # a running secretary is left alone

    def test_outside_tmux_selects_before_attaching(self):
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.cs.os, "execvp") as execvp:
            self.start()
        self.assertIn(("select-window", "-t", "%286"), self.calls)
        execvp.assert_called_once_with("tmux", ["tmux", "attach-session", "-t", "=claudesec"])

    def test_detached_start_leaves_the_users_view(self):
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1/default,1,0"}):
            self.start(detach=True)
        names = [c[0] for c in self.calls]
        self.assertNotIn("select-window", names)
        self.assertNotIn("switch-client", names)


if __name__ == "__main__":
    unittest.main()
