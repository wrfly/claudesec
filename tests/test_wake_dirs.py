"""Waking a worker whose directory is gone: a scratch dir under /tmp (wiped at reboot) is made
again, a moved one is followed (`claudesec redir`, `wake --dir`) together with its conversation,
and anything else is refused with the way to fix it.

    python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_external_sessions import load


class Stop(Exception):
    pass


class WakeDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "state"
        self.home.mkdir()
        self.cs = load(self.home)
        self.cs.SCRATCH_ROOTS = (self.root / "scratch",)
        self.claude = self.root / "claude"
        self.cs.claude_dir = lambda: self.claude
        self.old = str(self.root / "src" / "cmux")      # renamed away
        self.new = self.root / "src" / "claudesec"
        self.new.mkdir(parents=True)
        self.workers = {
            "cmux": {"status": "SHELVED", "dir": self.old, "session": "sid-1", "task": "t"},
            "adopt": {"status": "SHELVED", "dir": self.old + "/sub", "session": "sid-2", "task": "t"},
            "other": {"status": "SHELVED", "dir": self.old + "x", "session": "sid-3", "task": "t"},
        }
        self.save()
        self.cs.live_holder = lambda sid: None
        self.cs.prepare_worker = lambda *a, **k: (_ for _ in ()).throw(Stop())
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": self.workers}))

    def transcript(self, sid, cwd):
        d = self.cs.claude_project(cwd)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n")
        (d / sid / "subagents").mkdir(parents=True)
        return d

    def wake(self, name, **kw):
        args = argparse.Namespace(name=name, model=None, quiet=True, **kw)
        with self.assertRaises(Stop):  # stopped right before a window would open
            self.cs.cmd_wake(args)
        return self.cs.read_registry()[1][name]

    def test_a_scratch_dir_is_made_again(self):
        gone = self.root / "scratch" / "claudesec-work" / "vpsproxy"
        self.assertEqual(self.cs.where_now(str(gone))[0], str(gone))
        self.assertTrue(gone.is_dir())
        self.assertEqual(self.cs.where_now(str(gone)), (str(gone), None))

    def test_anything_else_gone_is_refused_with_the_fix(self):
        self.transcript("sid-1", self.old)
        with self.assertRaises(SystemExit) as e:
            self.cs.cmd_wake(argparse.Namespace(name="cmux", model=None, quiet=True))
        self.assertIn("claudesec redir", str(e.exception.code))
        self.assertIn("--dir", str(e.exception.code))
        self.assertFalse(Path(self.old).exists())

    def test_wake_dir_moves_the_worker_and_its_transcript(self):
        old_project = self.transcript("sid-1", self.old)
        w = self.wake("cmux", dir=str(self.new))
        self.assertEqual(w["dir"], str(self.new))
        moved = self.cs.claude_project(self.new)
        self.assertTrue((moved / "sid-1.jsonl").exists())
        self.assertTrue((moved / "sid-1" / "subagents").is_dir())
        self.assertFalse((old_project / "sid-1.jsonl").exists())
        self.assertEqual(self.cs.transcript("sid-1"), moved / "sid-1.jsonl")

    def test_wake_dir_must_exist(self):
        self.transcript("sid-1", self.old)
        with self.assertRaises(SystemExit) as e:
            self.cs.cmd_wake(argparse.Namespace(name="cmux", model=None, quiet=True, dir=str(self.root / "nope")))
        self.assertIn("no such directory", str(e.exception.code))

    def test_redir_points_every_worker_there_at_the_new_place(self):
        self.cs.cmd_redir(argparse.Namespace(old=self.old, new=str(self.new)))
        ws = self.cs.read_registry()[1]
        self.assertEqual(ws["cmux"]["dir"], str(self.new))
        self.assertEqual(ws["adopt"]["dir"], str(self.new) + "/sub")
        self.assertEqual(ws["other"]["dir"], self.old + "x")  # a sibling that only shares the prefix
        self.assertEqual(json.loads((self.home / "registry.json").read_text())["moved"], {self.old: str(self.new)})
        # the move is remembered for paths recorded elsewhere, such as an archived delegation
        (self.new / "sub").mkdir()
        self.assertEqual(self.cs.where_now(self.old + "/sub")[0], str(self.new / "sub"))

    def test_redir_then_wake_brings_the_transcript_along(self):
        self.transcript("sid-1", self.old)
        self.cs.cmd_redir(argparse.Namespace(old=self.old, new=str(self.new)))
        w = self.wake("cmux")
        self.assertEqual(w["dir"], str(self.new))
        self.assertNotIn("moved_from", w)
        self.assertEqual(self.cs.transcript("sid-1"), self.cs.claude_project(self.new) / "sid-1.jsonl")

    def test_redir_refuses_a_directory_still_there(self):
        Path(self.old).mkdir(parents=True)
        with self.assertRaises(SystemExit):
            self.cs.cmd_redir(argparse.Namespace(old=self.old, new=str(self.new)))
        self.assertEqual(self.cs.read_registry()[1]["cmux"]["dir"], self.old)

    def test_a_transcript_whose_start_dir_exists_stays_put(self):
        # e.g. a path Claude Code names its folder differently for: never move what may be in use
        here = self.root / "here"
        here.mkdir()
        project = self.transcript("sid-1", str(here))
        self.assertIsNone(self.cs.follow_conversation({"session": "sid-1"}, self.new))
        self.assertTrue((project / "sid-1.jsonl").exists())

    def test_a_cursor_chat_follows_its_worker(self):
        with mock.patch.dict(os.environ, HOME=str(self.root)):
            chats = self.root / ".cursor" / "chats"
            src = chats / hashlib.md5(self.old.encode()).hexdigest() / "chat-1"
            src.mkdir(parents=True)
            w = {"agent": "cursor", "session": "chat-1"}
            self.assertIsNone(self.cs.follow_conversation(w, self.new))  # nothing says where it was
            self.cs.follow_conversation(w, self.new, old=self.old)
            self.assertTrue((chats / hashlib.md5(str(self.new).encode()).hexdigest() / "chat-1").is_dir())
            self.assertFalse(src.exists())


if __name__ == "__main__":
    unittest.main()
