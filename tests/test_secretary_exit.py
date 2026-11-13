"""What the secretary's exit may put away, and who counts as the secretary. 11-13 17:11 the secretary
exited and shelve-all killed worker disk: at its prompt, its background Bash was running paced
DELETEs on a production database, and Claude Code's status for that, "shell", was taken for idle.
17:18-17:27 a Claude session debugging the secretary's failed start ran `claudesec run` in scratch
tmux sessions against the real state directory; each run woke the shelved workers and, as its
claude exited, shelved them all again. That start had failed because Popen, given the name
`claude`, skipped a launcher script it could not exec ("# !/bin/sh") for the next claude on PATH.

    python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_external_sessions import load

SESS = "claudesec"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"tmux_session": SESS, "language": "en"}))
        (self.home / "claude").mkdir()
        self.cs.claude_dir = lambda: self.home / "claude"

    def tearDown(self):
        self.tmp.cleanup()


class Status(Base):
    def conv(self, status):
        return {"pid": 4242, "sessionId": "sid", "status": status, "startedAt": 1}

    def test_a_background_shell_is_working(self):
        self.assertEqual(self.cs.session_status(self.conv("shell")), ("background", ["bg command"]))
        self.assertIn("bg tasks", self.cs.status_label(self.conv("shell")))

    def test_busy_and_waiting_are_working_idle_is_not(self):
        for status, working in (("busy", True), ("waiting", True), ("shell", True), ("idle", False)):
            self.cs.live_holder = lambda sid, s=status: self.conv(s)
            self.assertEqual(self.cs.worker_busy("w", {"session": "sid"}), working, status)

    def test_shelve_refuses_a_shell_and_a_waiting_one(self):
        for status, said in (("shell", "background tasks are still running"), ("waiting", "permission prompt")):
            with self.assertRaises(SystemExit) as e:
                self.cs.refuse_if_working("w", self.conv(status))
            self.assertIn(said, str(e.exception.code))


class ShelveAll(Base):
    """The secretary is gone: a worker with a background shell is waited for, then put away."""

    def setUp(self):
        super().setUp()
        cs = self.cs
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "disk": {"status": "PROGRESS", "task": "t", "dir": str(self.home), "session": "sid-disk",
                     "window": "@8", "model": "claude-opus-5-5", "started_at": "2026-11-13T16:20:00+08:00"},
            "eu-pay": {"status": "DONE", "task": "t", "dir": str(self.home), "session": "sid-eu",
                       "window": "@9", "model": "claude-opus-5-5", "started_at": "2026-11-13T16:20:00+08:00"}}}))
        for name in ("disk", "eu-pay"):
            (self.home / "workers" / name).mkdir(parents=True)
        self.live = {"sid-disk": {"pid": 1, "sessionId": "sid-disk", "name": "disk", "status": "shell", "startedAt": 1},
                     "sid-eu": {"pid": 2, "sessionId": "sid-eu", "name": "eu-pay", "status": "idle", "startedAt": 1}}
        self.stopped = []
        cs.claude_sessions = lambda: list(self.live.values())
        cs.conversation_exists = lambda w: True
        cs.current_run = lambda: None
        cs.tmux = lambda *a, **k: ""
        cs.window_place = lambda sess, window: None
        cs.detached_jobs = lambda sid, pid: []
        cs.stop_foreground = lambda s: self.stopped.append(s["name"]) or self.live.pop(s["sessionId"])

    def test_the_shell_is_waited_for(self):
        def sleep(secs):  # the next look, 10 s on: disk's script is done
            self.live["sid-disk"]["status"] = "idle"
        with mock.patch.object(self.cs.time, "sleep", sleep), contextlib.redirect_stdout(io.StringIO()) as out:
            self.cs.shelve_all(1)
        self.assertEqual(self.stopped, ["eu-pay", "disk"], out.getvalue())
        workers = self.cs.read_registry()[1]
        self.assertEqual({n: w["status"] for n, w in workers.items()}, {"disk": "SHELVED", "eu-pay": "SHELVED"})

    def test_the_secretary_back_leaves_it_running(self):
        def sleep(secs):  # a new secretary started while disk's script ran
            self.live["sid-sec"] = {"pid": 3, "sessionId": "sid-sec", "name": "secretary", "status": "idle"}
        with mock.patch.object(self.cs.time, "sleep", sleep), contextlib.redirect_stdout(io.StringIO()):
            self.cs.shelve_all(1)
        self.assertEqual(self.stopped, ["eu-pay"])
        self.assertEqual(self.cs.read_registry()[1]["disk"]["status"], "PROGRESS")


class Run(Base):
    """cmd_run up to its claude, with what it detaches recorded."""

    def setUp(self):
        super().setUp()
        self.cs.fresh_prompt = lambda: []
        self.cs.memory_settings = lambda d: {}
        self.cs.arrange_dashboard = lambda sess: None
        self.detached = []

    def run_secretary(self, popen=None, pane=True, which="/x/claude"):
        patches = [mock.patch.object(self.cs, "detach", self.detached.append),
                   mock.patch.object(self.cs, "in_secretary_pane", lambda sess: pane),
                   mock.patch.object(self.cs.signal, "signal", lambda *a: None),
                   mock.patch.object(self.cs.os, "chdir", lambda d: None),
                   mock.patch.object(self.cs.shutil, "which", lambda name: which),
                   mock.patch.object(sys, "stdin", io.StringIO()),
                   mock.patch.dict(os.environ, {"TMUX_PANE": ""})]
        if popen:
            patches.append(mock.patch.object(self.cs.subprocess, "Popen", popen))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.cs.cmd_run(argparse.Namespace(new=False))

    def child(self, on_wait=None):
        started = []

        class Child:
            def __init__(self, argv, **kw):
                started.append(argv)

            def wait(self):
                on_wait and on_wait()
                return 0

            def terminate(self):
                pass
        return Child, started

    def test_outside_the_secretary_pane_nothing_starts(self):
        Child, started = self.child()
        with self.assertRaises(SystemExit) as e:
            self.run_secretary(Child, pane=False)
        self.assertIn("runs only in the secretary's pane", str(e.exception.code))
        self.assertEqual((started, self.detached), ([], []))
        self.assertFalse((self.home / "run.pid").exists())

    def test_claude_is_started_by_its_path(self):
        Child, started = self.child()
        self.run_secretary(Child)
        self.assertEqual(started[0][0], "/x/claude")
        self.assertEqual(self.detached, ["wake-all", "reminders", "shelve-all"])

    def test_a_restart_leaves_the_workers_running(self):
        Child, _ = self.child(on_wait=lambda: (self.home / "restarting").write_text(str(__import__("time").time())))
        self.run_secretary(Child)
        self.assertEqual(self.detached, ["wake-all", "reminders"])
        self.assertFalse((self.home / "restarting").exists())

    def test_a_launcher_that_cannot_be_execd_is_not_passed_over(self):
        broken, good = self.home / "a", self.home / "b"
        for d, first in ((broken, "# !/bin/sh"), (good, "#!/bin/sh")):
            d.mkdir()
            (d / "claude").write_text(f"{first}\ntouch {self.home}/ran-{d.name}\n")
            (d / "claude").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{broken}:{good}:/usr/bin:/bin"}):
            import shutil
            exe = shutil.which("claude")
            with self.assertRaises(SystemExit) as e:
                self.run_secretary(which=exe)
        self.assertEqual(e.exception.code, 1)
        self.assertEqual(sorted(p.name for p in self.home.glob("ran-*")), [])
        self.assertFalse((self.home / "run.pid").exists())  # wake-all wakes nothing for a run that never ran
        self.assertNotIn("shelve-all", self.detached)


class SecretaryPane(unittest.TestCase):
    """in_secretary_pane against a tmux server of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.sock = str(home / "tmux.sock")
        self.cs = cs = load(home)
        (home / "config.json").write_text(json.dumps({"tmux_session": SESS, "language": "en"}))
        cs.tmux = lambda *a, check=True: subprocess.run(["tmux", "-f", "/dev/null", "-S", self.sock, *a],
                                                        capture_output=True, text=True, check=check).stdout
        cs.has_session = lambda name: subprocess.run(
            ["tmux", "-S", self.sock, "has-session", "-t", f"={name}"], capture_output=True).returncode == 0

    def tearDown(self):
        subprocess.run(["tmux", "-S", self.sock, "kill-server"], capture_output=True)
        self.tmp.cleanup()

    def pane(self, *a):
        return self.cs.tmux(*a, "-P", "-F", "#{pane_id}", "sleep 600").strip()

    def test_only_the_secretary_pane(self):
        sec = self.pane("new-session", "-d", "-s", SESS, "-n", "secretary", "-x", "200", "-y", "50")
        other = self.pane("new-window", "-d", "-t", f"={SESS}:", "-n", "claude.exe")
        scratch = self.pane("new-session", "-d", "-s", "cs-debug", "-n", "secretary")
        for pane, want in ((sec, True), (other, False), (scratch, False), ("", False)):
            with mock.patch.dict(os.environ, {"TMUX_PANE": pane}):
                self.assertEqual(self.cs.in_secretary_pane(SESS), want, pane)
        self.cs.tmux("set-option", "-p", "-t", sec, "@claudesec-role", "secretary")
        with mock.patch.dict(os.environ, {"TMUX_PANE": sec}):
            self.assertTrue(self.cs.in_secretary_pane(SESS))

    def test_no_session(self):
        with mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertFalse(self.cs.in_secretary_pane(SESS))


if __name__ == "__main__":
    unittest.main()
