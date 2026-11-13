"""`claudesec rename OLD NEW` gives a worker another name while it runs: the registry (same place in
the table), its tmux window, workers/ and reports/, pending reminders, open queue items and to-dos
tagged [OLD], and a Claude Code worker's session name, through the session's messaging socket so
neither its conversation nor its input box is touched. Its process keeps calling hooks and
`claudesec report` with the old name until it is restarted: an alias, and workers/NEW a symlink to
the directory its paths point at, until it is shelved, closed or woken.

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

QUEUE = """# 决策队列

## 待问

- #5 [talk] 要不要把审计日志留 7 天
- #6 [talker] 不是它的

## 已处理

- #2 [talk] 早先一个也叫 talk 的 worker 的事
"""

NOTICES = """# 待办事项

## 待办

- #7 [talk] 重启那 8 个会话

## 已完成

- #3 [talk] 2026-11-01 旧的
"""


class Rename(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh", "secretary_name": "secretary"}))
        self.cs.secretary_alive = lambda: False
        self.calls, self.frames = [], []
        self.cs.tmux = lambda *a, **k: self.calls.append(a) or ""
        self.cs.alive_windows = lambda: {"@2", "@3"}
        self.running = {"talk": True, "ext": True}
        self.cs.agent_running = lambda w: self.running.get(w.get("_n"), False)
        self.sessions = [{"pid": 4242, "sessionId": "sid-talk", "name": "talk", "status": "busy",
                          "messagingSocketPath": "/run/x/4242.sock", "tmux": "claudesec:@2.%2"}]
        self.cs.claude_sessions = lambda: self.sessions
        self.cs.message_socket = self.deliver
        self.sleep = mock.patch.object(self.cs.time, "sleep", lambda s: None)
        self.sleep.start()
        (self.home / "registry.json").write_text(json.dumps({"focus": "talk", "workers": {
            "first": {"status": "DONE", "task": "t", "model": "claude-opus-5-5", "window": None, "dir": str(self.home)},
            "talk": {"_n": "talk", "status": "DONE", "mode": "discuss", "task": "聊审计", "model": "claude-opus-5-5",
                     "window": "@2", "dir": str(self.home), "session": "sid-talk", "agent": "claude",
                     "started_at": "2026-11-13T11:02:44+08:00", "archived": "2026-11/2026-11-13-talk.md"},
            "kid": {"status": "PROGRESS", "task": "t", "model": "x", "window": None, "dir": str(self.home),
                    "agent": "cursor", "delegated_by": "talk"},
            "ext": {"_n": "ext", "status": "PROGRESS", "task": "t", "model": "x", "window": "@3", "dir": str(self.home),
                    "agent": "cursor", "session": "chat-1"},
            "last": {"status": "DONE", "task": "t", "model": "claude-opus-5-5", "window": None, "dir": str(self.home)}}}))
        self.cs.prepare_worker("talk", {"secretary_name": "secretary"}, "hi", self.home)
        self.cs.append_log("talk", "task", "聊审计")
        (self.home / "queue.md").write_text(QUEUE)
        (self.home / "notices.md").write_text(NOTICES)
        with self.cs.reminders() as data:
            data["items"] += [
                {"id": "r1", "due": "2026-11-14T09:00:00+08:00", "target": "talk", "text": "a", "every": None, "status": "pending"},
                {"id": "r2", "due": "2026-11-12T09:00:00+08:00", "target": "talk", "text": "b", "every": None, "status": "fired"},
                {"id": "r3", "due": "2026-11-14T09:00:00+08:00", "target": "secretary", "text": "c", "every": None, "status": "pending"}]

    def tearDown(self):
        self.sleep.stop()
        self.tmp.cleanup()

    def deliver(self, path, frames):
        self.frames.append((path, frames))
        for s in self.sessions:
            if s["messagingSocketPath"] == path and frames[-1].get("session_id") == s["sessionId"]:
                s["name"] = frames[-1]["name"]

    def rename(self, old="talk", new="claude-mod-audit"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cs.cmd_rename(SimpleNamespace(old=old, new=new))
        return out.getvalue()

    def refused(self, old="talk", new="claude-mod-audit"):
        before = (self.home / "registry.json").read_text()
        with self.assertRaises(SystemExit) as e:
            self.rename(old, new)
        self.assertEqual((self.home / "registry.json").read_text(), before)
        return str(e.exception.code)

    def hook(self, event, name, payload):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.cs.cmd_hook(SimpleNamespace(event=event, name=name))
        finally:
            sys.stdin = old

    def workers(self):
        return self.cs.read_registry()

    def test_a_running_claude_worker_is_renamed_everywhere_without_a_restart(self):
        out = self.rename()
        # The session, through its socket: no keys sent to its window.
        self.assertEqual(self.frames[0][0], "/run/x/4242.sock")
        self.assertEqual(self.frames[0][1][-1], {"type": "control", "action": "rename", "name": "claude-mod-audit",
                                                 "session_id": "sid-talk"})
        self.assertFalse([c for c in self.calls if c[0] in ("send-keys", "paste-buffer")])
        focus, ws = self.workers()
        self.assertEqual(list(ws), ["first", "claude-mod-audit", "kid", "ext", "last"])  # same place in the table
        self.assertEqual(focus, "claude-mod-audit")
        w = ws["claude-mod-audit"]
        self.assertEqual((w["status"], w["mode"], w["session"], w["archived"]),
                         ("DONE", "discuss", "sid-talk", "2026-11/2026-11-13-talk.md"))
        self.assertEqual(self.cs.former_names(w), ["talk"])
        self.assertEqual(ws["kid"]["delegated_by"], "claude-mod-audit")
        self.assertIn(("rename-window", "-t", "@2", "claude-mod-audit"), self.calls)
        self.assertIn(("set-option", "-w", "-t", "@2", "automatic-rename", "off"), self.calls)
        # Its process still looks in workers/talk/: workers/NEW points there.
        new_dir, old_dir = self.home / "workers" / "claude-mod-audit", self.home / "workers" / "talk"
        self.assertTrue(new_dir.is_symlink())
        self.assertTrue(old_dir.is_dir() and not old_dir.is_symlink())
        self.assertEqual(new_dir.resolve(), old_dir.resolve())
        self.assertEqual(self.cs.resolve("talk"), "claude-mod-audit")
        self.assertFalse((self.home / "reports" / "talk.log").exists())
        self.assertIn("聊审计", (self.home / "reports" / "claude-mod-audit.log").read_text())
        items = {r["id"]: r["target"] for r in self.cs.read_reminders()}
        self.assertEqual(items, {"r1": "claude-mod-audit", "r2": "talk", "r3": "secretary"})
        queue, notices = (self.home / "queue.md").read_text(), (self.home / "notices.md").read_text()
        self.assertIn("- #5 [claude-mod-audit] 要不要", queue)
        self.assertIn("- #6 [talker]", queue)
        self.assertIn("- #2 [talk] 早先", queue)  # closed: maybe another talk's
        self.assertIn("- #7 [claude-mod-audit] 重启", notices)
        self.assertIn("- #3 [talk] 2026-11-01", notices)
        self.assertIn("SendMessage(to: 'claude-mod-audit')", out)

    def test_its_hooks_and_reports_under_the_old_name_reach_the_new_one(self):
        self.rename()
        self.hook("worker-report", "talk", {"tool_input": {"to": "secretary", "message": "TYPE: DONE\nDID: 讨论完了"}})
        w = self.workers()[1]["claude-mod-audit"]
        self.assertEqual(w["last_summary"], "讨论完了")
        self.assertNotIn("talk", self.workers()[1])
        self.assertFalse((self.home / "reports" / "talk.log").exists())
        self.assertIn("讨论完了", (self.home / "reports" / "claude-mod-audit.log").read_text())
        self.assertTrue((self.home / "workers" / "talk" / "reported").exists())  # the path its hooks test
        self.hook("worker-stop", "talk", {"session_id": "sid-talk"})  # discussion: let through, no error

    def test_shelving_it_moves_the_directory_over_and_frees_the_old_name(self):
        self.rename()
        with self.assertRaises(SystemExit) as e:
            with contextlib.redirect_stdout(io.StringIO()):
                self.cs.cmd_spawn(SimpleNamespace(name="talk", dir=None, model=None, discuss=False, agent=None,
                                                  for_worker=None))
        self.assertIn("pick another name", str(e.exception.code))
        self.sessions[0]["status"] = "idle"
        self.cs.conversation_exists = lambda w: True
        self.cs.refuse_if_working = lambda name, conv: None
        self.cs.stop_foreground = lambda s: None
        self.cs.worker_leftovers = lambda name, w, shared=False: []
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_shelve(SimpleNamespace(name="claude-mod-audit", note="n", as_name=None, force=False))
        new_dir = self.home / "workers" / "claude-mod-audit"
        self.assertTrue(new_dir.is_dir() and not new_dir.is_symlink())
        self.assertTrue((new_dir / "prompt.md").exists())
        self.assertFalse((self.home / "workers" / "talk").exists())
        self.assertEqual(self.cs.renamed_aliases(), {})
        self.assertNotIn("renamed", json.loads((self.home / "registry.json").read_text()))
        self.assertEqual(self.cs.resolve("talk"), "talk")

    def test_waking_it_writes_its_rules_and_hooks_under_the_new_name(self):
        self.rename()
        self.running["talk"] = False
        d = self.cs.prepare_worker("claude-mod-audit", {"secretary_name": "secretary"}, "wake", self.home, fresh_log=False)
        self.assertEqual(d, self.home / "workers" / "claude-mod-audit")
        self.assertFalse(d.is_symlink())
        self.assertIn("hook worker-stop claude-mod-audit", (d / "settings.json").read_text())
        self.assertIn("claude-mod-audit", (d / "prompt.md").read_text())
        self.assertFalse((self.home / "workers" / "talk").exists())

    def test_renamed_again_while_it_runs_the_first_name_still_reaches_it(self):
        self.rename()
        self.rename("claude-mod-audit", "cc-audit")
        self.assertEqual(self.cs.renamed_aliases(), {"talk": "cc-audit"})
        self.assertFalse((self.home / "workers" / "claude-mod-audit").exists())
        self.assertEqual((self.home / "workers" / "cc-audit").resolve(), (self.home / "workers" / "talk").resolve())
        self.assertEqual(self.cs.former_names(self.workers()[1]["cc-audit"]), ["talk", "claude-mod-audit"])
        self.rename("cc-audit", "talk")  # back to the name its process started with
        self.assertEqual(self.cs.renamed_aliases(), {})
        self.assertFalse((self.home / "workers" / "cc-audit").exists())
        self.assertFalse((self.home / "workers" / "talk").is_symlink())
        self.assertEqual(self.sessions[0]["name"], "talk")

    def test_a_stopped_worker_is_moved_at_once_with_no_alias(self):
        self.running["talk"] = False
        self.sessions.clear()
        out = self.rename()
        self.assertEqual(self.frames, [])
        d = self.home / "workers" / "claude-mod-audit"
        self.assertTrue(d.is_dir() and not d.is_symlink())
        self.assertFalse((self.home / "workers" / "talk").exists())
        self.assertEqual(self.cs.renamed_aliases(), {})
        self.assertIn("下次 wake", out)

    def test_an_external_agent_worker_keeps_reporting_under_its_old_name(self):
        self.cs.prepare_worker("ext", {"secretary_name": "secretary"}, "hi", self.home, agent=self.cs.AGENTS["cursor"])
        self.rename("ext", "ext2")
        self.assertEqual(self.frames, [])  # no Claude Code session to rename
        with mock.patch.dict(os.environ, {"CLAUDESEC_WORKER": "ext"}):
            self.assertEqual(self.cs.hooked_worker(), "ext2")
        old = sys.stdin
        sys.stdin = io.StringIO("TYPE: PROGRESS\nDID: 半路\nNEXT: 接着")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.cs.cmd_report(SimpleNamespace(name="ext"))
        finally:
            sys.stdin = old
        self.assertEqual(self.workers()[1]["ext2"]["last_summary"], "半路")

    def test_the_session_not_renamed_changes_nothing(self):
        self.cs.message_socket = lambda path, frames: (_ for _ in ()).throw(ConnectionRefusedError("refused"))
        self.assertIn("NOT RENAMED", self.refused())
        self.assertEqual(self.sessions[0]["name"], "talk")
        self.assertFalse((self.home / "workers" / "claude-mod-audit").exists())

    def test_a_running_claude_worker_with_no_live_session_is_refused(self):
        self.sessions.clear()
        self.assertIn("no live Claude Code session", self.refused())

    def test_bad_or_taken_names_are_refused(self):
        self.assertIn("bad worker name", self.refused(new="Claude_Mod"))
        self.assertIn("secretary", self.refused(new="secretary"))
        self.assertIn("taken by another worker", self.refused(new="first"))
        self.assertIn("no worker", self.refused(old="nobody"))
        self.sessions.append({"pid": 9, "sessionId": "other", "name": "claude-mod-audit", "messagingSocketPath": "/s"})
        self.assertIn("already called claude-mod-audit", self.refused())
        self.sessions.pop()
        self.rename()
        self.assertIn("process still calls its hooks", self.refused(old="first", new="talk"))
        self.assertIn("it is called claude-mod-audit now", self.refused(old="talk", new="x"))

    def test_what_it_left_under_its_old_name_is_still_its_own(self):
        self.rename()
        p = SimpleNamespace(cwd="/tmp/claude-talk/server", argv=["python3", "-m", "http.server"], env={}, ppid=1,
                            start=0, comm="python3", rss=0)
        found = self.cs.leftovers("claude-mod-audit", snap={99: p}, keep=set(), containers=[
            {"id": "c1", "name": "x", "labels": {"claudesec.worker": "talk"}, "mounts": [], "created": None}])
        self.assertEqual({f["id"] for f in found}, {99, "c1"})

    def test_a_later_archive_updates_its_record_and_says_the_old_name(self):
        self.rename()
        (self.home / "history" / "2026-11").mkdir(parents=True)
        (self.home / "history" / "2026-11" / "2026-11-13-talk.md").write_text("old record")
        self.cs.worker_leftovers = lambda name, w, shared=False: []
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_archive(SimpleNamespace(name="claude-mod-audit", subagent=False, summary="s", tags="a",
                                                files="", dir=None, task=None))
        text = (self.home / "history" / "2026-11" / "2026-11-13-talk.md").read_text()
        self.assertIn('worker: "claude-mod-audit"', text)
        self.assertIn('renamed_from: ["talk"]', text)
        self.assertIn("· claude-mod-audit · ", (self.home / "history" / "INDEX.md").read_text())


if __name__ == "__main__":
    unittest.main()
