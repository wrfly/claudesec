"""config remote_control starts the secretary, and with "all" the Claude workers, with AgentLodge's
`--rc`; the development-channels warning that comes with it is answered with the option that goes
on, never Exit, and only when it is the dialog at the bottom of the screen.

    python3 -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_external_sessions import load

# Captured from claude 2.1.312 started with --dangerously-load-development-channels server:agentlodge.
WARNING = """
──────────────────────────────────────────────────────────────────────────────
  WARNING: Loading development channels

  --dangerously-load-development-channels is for local channel development only. Do not use this option to run channels you have downloaded
  off the internet.

  Please use --channels to run a list of approved channels.

  Channels: server:agentlodge

  ❯ 1. I am using this for local development
    2. Exit

  Enter to confirm · Esc to cancel
"""

# The warning quoted in a conversation, with the input box below it.
QUOTED = """
● The box says "WARNING: Loading development channels" and offers
  "I am using this for local development" or Exit.

────────────────────────────────────────
❯
────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
"""

BOX = """
● Ready.

────────────────────────────────────────
❯
────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
"""


class RemoteControl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.cs.fresh_prompt = lambda: []
        self.workers = {"plans": {"status": "STARTING", "window": "@57", "model": "claude-opus-5-5",
                                  "session": None, "task": "t", "last_report_at": None}}
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": self.workers}))
        self.screen, self.keys = WARNING, []
        self.cs.pane_text = lambda target, suggestion="mark": self.screen
        self.cs.alive_windows = lambda: {"@57"}
        patcher = mock.patch.object(self.cs.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cs.tmux = self.fake_tmux

    def tearDown(self):
        self.tmp.cleanup()

    def fake_tmux(self, *args, check=True):
        if args[0] == "display":
            return "0 1"
        if args[0] == "send-keys":
            key = args[-1]
            self.keys.append(key)
            if key in ("Up", "Down"):
                self.screen = (self.screen.replace("❯ 1.", "  1.").replace("  2. Exit", "❯ 2. Exit")
                               if key == "Down" else
                               self.screen.replace("  1.", "❯ 1.").replace("❯ 2. Exit", "  2. Exit"))
            if key == "Enter":
                self.screen = BOX
        return ""

    def config(self, **kw):
        (self.home / "config.json").write_text(json.dumps(kw))
        return self.cs.config()

    def test_off_by_default(self):
        cfg = self.config()
        self.assertFalse(self.cs.remote_control(cfg))
        self.assertFalse(self.cs.remote_control(cfg, secretary=True))
        self.assertNotIn("--rc", self.cs.AGENTS["claude"].command("w", "claude-opus-5-5", self.home, cfg))

    def test_secretary_only(self):
        cfg = self.config(remote_control="secretary")
        self.assertTrue(self.cs.remote_control(cfg, secretary=True))
        self.assertNotIn("--rc", self.cs.AGENTS["claude"].command("w", "claude-opus-5-5", self.home, cfg))

    def test_all_puts_it_on_workers_too(self):
        for want in ("all", True):
            cfg = self.config(remote_control=want)
            self.assertTrue(self.cs.remote_control(cfg, secretary=True))
            cmd = self.cs.AGENTS["claude"].command("w", "claude-opus-5-5", self.home, cfg, resume="sid", prompt=False)
            self.assertIn("--rc --resume sid", cmd)

    def test_the_secretary_starts_with_it(self):
        self.config(remote_control="secretary")
        started = []

        class Child:
            def __init__(self, argv, **kw):
                started.append(argv)

            def wait(self):
                return 0

        with mock.patch.object(self.cs.subprocess, "Popen", Child), \
                mock.patch.object(self.cs, "detach", lambda job: None), \
                mock.patch.object(self.cs, "memory_settings", lambda d: {}), \
                mock.patch.object(self.cs.signal, "signal", lambda *a: None), \
                mock.patch.object(self.cs.os, "chdir", lambda d: None), \
                mock.patch.dict(os.environ, {"TMUX_PANE": ""}):
            self.cs.cmd_run(self.cs.argparse.Namespace(new=False))
        self.assertIn("--rc", started[0])
        self.assertEqual(started[0][0], "claude")

    def test_the_warning_is_answered_with_enter(self):
        self.assertTrue(self.cs.answer_channels_dialog("@57"))
        self.assertEqual(self.keys, ["Enter"])

    def test_never_enter_on_exit(self):
        self.screen = WARNING.replace("❯ 1.", "  1.").replace("  2. Exit", "❯ 2. Exit")
        self.assertTrue(self.cs.answer_channels_dialog("@57"))
        self.assertEqual(self.keys, ["Up", "Enter"])

    def test_a_quoted_warning_is_left_alone(self):
        self.screen = QUOTED
        self.assertFalse(self.cs.answer_channels_dialog("@57"))
        self.assertFalse(self.cs.answer_channels_dialog("@57", BOX))
        self.assertEqual(self.keys, [])

    def test_spawn_check_goes_past_it(self):
        self.assertFalse(self.cs.stuck_on_dialog("plans", "@57", self.cs.AGENTS["claude"]))
        self.assertEqual(self.keys, ["Enter"])

    def test_watchdog_answers_it_without_an_alert(self):
        self.assertEqual(self.cs.check_stuck(now_t=1000), [])
        self.assertEqual(self.keys, ["Enter"])
        self.assertFalse((self.home / "alerts").exists())

    def test_the_secretary_answers_its_own(self):
        self.cs.accept_channels("%1", secs=3)
        self.assertEqual(self.keys, ["Enter"])


if __name__ == "__main__":
    unittest.main()
