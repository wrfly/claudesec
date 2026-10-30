"""Delegating again to a Cursor worker that finished, was archived and closed: resume its chat in
its own directory, never spawn a blank one under the same name.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class DelegateArchived(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "state"
        self.home.mkdir()
        self.cs = load(self.home)
        self.project = Path(self.tmp.name) / "wt"
        self.project.mkdir()
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "boss": {"status": "PROGRESS", "dir": str(Path(self.tmp.name)), "task": "t"}}}))
        hist = self.home / "history" / "2026-10"
        hist.mkdir(parents=True)
        # as archived before delegated_by was recorded: the index line names the delegator
        (hist / "2026-10-23-m1.md").write_text(self.cs.frontmatter(
            date="2026-10-23", project=str(self.project), worker="m1", model="gpt-5.6-sol-high",
            session="chat-1", status="DONE", files=[], tags=["delegated", "cursor"]) + "## 任务\nx\n")
        (self.home / "history" / "INDEX.md").write_text(
            "- 2026-10-23 · wt · m1 · DONE · 为 boss：did M1 · #delegated #cursor → 2026-10/2026-10-23-m1.md\n")
        self.cs.AGENTS["cursor"].available = lambda: (True, "test")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def delegate(self, boss="boss", name="m1", task="追加：再改一处"):
        out, saved_argv, saved_stdin = io.StringIO(), sys.argv, sys.stdin
        sys.argv, sys.stdin = ["claudesec", "delegate", boss, "--name", name], io.StringIO(task)
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        return out.getvalue()

    def test_finds_the_archive_and_its_delegator(self):
        past = self.cs.archived_delegation("m1")
        self.assertEqual((past["project"], past["session"], past["delegated_by"]), (str(self.project), "chat-1", "boss"))
        self.assertIsNone(self.cs.archived_delegation("m2"))

    def test_resumes_the_chat_in_its_own_directory(self):
        self.cs.cursor_chat_exists = lambda chat: chat == "chat-1"
        self.cs.cmd_spawn = lambda args: self.fail("spawned a blank worker")

        def wake(args):
            w = self.cs.read_registry()[1][args.name]
            self.calls.append((args.name, w["dir"], w["session"], w["delegated_by"], args.instruction))
            print("woke m1")
        self.cs.cmd_wake = wake
        out = self.delegate()
        self.assertEqual(self.calls, [("m1", str(self.project), "chat-1", "boss", "追加：再改一处")])
        self.assertIn("resumed", out)
        self.assertTrue((self.cs.worker_dir("m1") / "delegation.json").exists())

    def test_refuses_with_a_hint_when_the_chat_is_gone(self):
        self.cs.cursor_chat_exists = lambda chat: False
        self.cs.cmd_spawn = lambda args: self.fail("spawned a blank worker")
        with self.assertRaises(SystemExit) as e:
            self.delegate()
        self.assertIn("new --name", str(e.exception.code))
        self.assertIn(str(self.project), str(e.exception.code))
        self.assertNotIn("m1", self.cs.read_registry()[1])

    def test_someone_elses_past_delegation_is_not_theirs_to_resume(self):
        reg = json.loads((self.home / "registry.json").read_text())
        reg["workers"]["other"] = {"status": "PROGRESS", "dir": str(Path(self.tmp.name)), "task": "t"}
        (self.home / "registry.json").write_text(json.dumps(reg))
        with self.assertRaises(SystemExit) as e:
            self.delegate(boss="other")
        self.assertIn("boss's delegation", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
