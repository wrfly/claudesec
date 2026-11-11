"""`claudesec model` / `wake --model` move a Claude Code conversation between plain Claude Code and
`ttadk claude` by the model (2026-11-12: workers on ttadk + deepseek switched to claude-opus-5-5 kept
ttadk, which refused the model); a failed wake closes its window; --effort reaches `claude --effort`;
a wake with nothing on a socket stdin doesn't wait on it.

    python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class ModelSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        self.calls, self.cmds, self.verdict = [], [], "ok"

        def tmux(*a, **k):
            self.calls.append(a)
            if a[0] == "new-window":
                return "@9 %9"
            if a[0] == "respawn-pane":
                self.cmds.append(a[-1])
            return ""
        cs.tmux = tmux
        cs.has_session = lambda s: False
        cs.conversation_exists = lambda w: True
        cs.where_now = lambda cwd: (cwd, None)
        cs.live_holder = lambda sid: None
        cs.follow_conversation = lambda *a, **k: None
        cs.ensure_name = lambda name, pane, sid: name
        for obj in [*cs.AGENTS.values(), *cs.TTADK_RUNTIMES.values()]:
            obj.require = obj.room = obj.preflight = lambda *a, **k: None
            obj.confirm_resume = lambda name, pane, d, sid: (self.verdict, "SCREEN")

    def tearDown(self):
        self.tmp.cleanup()

    def worker(self, **w):
        base = {"status": "SHELVED", "dir": str(self.home), "session": "sid", "task": "t", "window": None}
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {"w": {**base, **w}}}))

    def wake(self, model=None, effort=None):
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_wake(argparse.Namespace(name="w", model=model, quiet=True, effort=effort))
        return self.cs.read_registry()[1]["w"]

    def test_ttadk_deepseek_to_opus_runs_claude_code(self):
        for rec in ({"agent": None}, {"agent": "claude"}, {"agent": "ttadk", "runtime": "claude"}):
            self.worker(model="deepseek-v4.1-flash", **rec)
            w = self.wake(model="claude-opus-5-5")
            self.assertNotIn("ttadk", self.cmds[-1], rec)
            self.assertIn("--model claude-opus-5-5", self.cmds[-1])
            self.assertEqual((w["agent"], w.get("runtime")), ("claude", None), rec)
            self.assertEqual(self.cs.agent_of(w).name, "claude")

    def test_opus_to_deepseek_runs_ttadk_claude(self):
        self.worker(model="claude-opus-5-5", agent="claude")
        w = self.wake(model="deepseek-v4.1-flash")
        self.assertIn("ttadk", self.cmds[-1])
        self.assertEqual((w["agent"], w["runtime"]), ("ttadk", "claude"))

    def test_a_codex_conversation_isnt_moved_to_claude(self):
        self.worker(model="gpt-6.1-sol", agent="ttadk", runtime="codex")
        with self.assertRaises(SystemExit) as e:
            self.wake(model="claude-opus-5-5")
        self.assertIn("Codex", str(e.exception.code))

    def test_a_failed_wake_closes_its_window(self):
        self.worker(model="claude-opus-5-5", agent="claude")
        self.verdict = "claude exited"
        with self.assertRaises(SystemExit) as e:
            self.wake()
        self.assertIn(("kill-window", "-t", "@9"), self.calls)
        self.assertIn("SCREEN", str(e.exception.code))
        self.assertEqual(self.cs.read_registry()[1]["w"]["status"], "SHELVED")

    def test_effort_is_kept_for_the_next_wake(self):
        self.worker(model="claude-opus-5-5", agent="claude")
        w = self.wake(effort="high")
        self.assertIn("--effort high", self.cmds[-1])
        self.assertEqual(w["effort"], "high")
        self.worker(**{**w, "status": "SHELVED"})
        self.wake()
        self.assertIn("--effort high", self.cmds[-1])

    def test_nothing_on_a_socket_stdin_is_no_text(self):
        a, b = socket.socketpair()
        saved = sys.stdin
        try:
            sys.stdin = os.fdopen(a.fileno(), "r", closefd=False)
            self.assertEqual(self.cs.stdin_text(wait=0.2), "")
            b.sendall(b"go on\n")
            b.shutdown(socket.SHUT_WR)
            self.assertEqual(self.cs.stdin_text(wait=0.2), "go on")
        finally:
            sys.stdin = saved
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
