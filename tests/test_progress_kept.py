"""A worker's PROGRESS is recorded and not sent to the secretary as a message: delivered, it showed
up in the secretary's window, and Claude Code pushed the silent secretary to say something about it.
The worker sends it with `report` (a refused SendMessage shows in its window as "Error:"), and the
secretary reads it, out of the user's sight, at the start of its next turn.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from test_external_sessions import load


class ProgressKept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "STARTING", "dir": "/tmp", "task": "t", "session": "s", "window": "@1"},
            "cur": {"status": "STARTING", "dir": "/tmp", "task": "t", "agent": "cursor", "window": "@2"},
            "sub": {"status": "STARTING", "dir": "/tmp", "task": "t", "agent": "cursor", "window": "@3",
                    "delegated_by": "w"}}}))
        self.alerts = []
        self.cs.raise_alert = self.alerts.append
        self.cs.secretary_alive = lambda: True
        self.cs.tmux = lambda *a, **k: ""

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, fn, stdin, *args):
        out, saved = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                fn(*args)
        finally:
            sys.stdin = saved
        return out.getvalue()

    def send(self, to, message):
        out = self.run_hook(self.cs.hook_worker_send, json.dumps(
            {"tool_name": "SendMessage", "tool_input": {"to": to, "message": message}}), "w")
        return json.loads(out)["hookSpecificOutput"] if out.strip() else None

    def test_progress_to_the_secretary_is_recorded_and_refused(self):
        got = self.send("secretary", "TYPE: PROGRESS\nDID: read the code\nNEXT: fix it")
        self.assertEqual(got["permissionDecision"], "deny")
        self.assertIn("don't resend", got["permissionDecisionReason"])
        self.assertIn(" report w <<'EOF'", got["permissionDecisionReason"])  # how to send the next one
        w = self.cs.read_registry()[1]["w"]
        self.assertEqual((w["status"], w["last_summary"]), ("PROGRESS", "read the code"))
        self.assertIn("NEXT: fix it", self.cs.read_log("w")[-1][1])

    def test_other_reports_and_other_recipients_go_through(self):
        for typ in ("DONE", "FAILED", "BLOCKED", "NEED_DECISION"):
            self.assertIsNone(self.send("secretary", f"TYPE: {typ}\nDID: x"))
        self.assertIsNone(self.send("secretary", "no type at all"))
        self.assertIsNone(self.send("other-worker", "TYPE: PROGRESS\nDID: x"))
        self.assertEqual(self.cs.read_registry()[1]["w"]["status"], "STARTING")  # PostToolUse records those

    def test_settings_hook_sendmessage_before_it_runs(self):
        self.cs.memory_settings = lambda cwd: {}
        d = self.cs.prepare_worker("w", self.cs.config(), "task", "/tmp")
        pre = json.loads((d / "settings.json").read_text())["hooks"]["PreToolUse"]
        self.assertTrue(any(e.get("matcher") == "SendMessage" and "worker-send" in e["hooks"][0]["command"]
                            for e in pre))

    def report(self, name, text):
        return self.run_hook(self.cs.cmd_report, text, types.SimpleNamespace(name=name))

    def secretary_turn(self, prompt):
        out = self.run_hook(self.cs.hook_secretary_prompt, json.dumps({"prompt": prompt}))
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    def test_cursor_progress_raises_no_alert(self):
        self.assertIn("recorded", self.report("cur", "TYPE: PROGRESS\nDID: halfway\nNEXT: rest"))
        self.assertEqual(self.alerts, [])
        self.assertEqual(self.cs.read_registry()[1]["cur"]["status"], "PROGRESS")
        self.assertIn("reported to", self.report("cur", "TYPE: DONE\nDID: all of it\nRESULT: ok\nDECISIONS: none"))
        self.assertEqual(len(self.alerts), 1)

    def test_a_claude_worker_reports_progress_and_only_progress(self):
        self.assertIn("recorded", self.report("w", "TYPE: PROGRESS\nDID: halfway\nNEXT: rest"))
        self.assertEqual(self.cs.read_registry()[1]["w"]["status"], "PROGRESS")
        with self.assertRaises(SystemExit) as e:
            self.report("w", "TYPE: DONE\nDID: all\nRESULT: ok\nDECISIONS: none")
        self.assertIn("SendMessage", str(e.exception))
        self.assertEqual(self.cs.read_registry()[1]["w"]["status"], "PROGRESS")
        self.assertEqual(self.alerts, [])

    def test_the_secretary_sees_progress_on_its_next_turn_once(self):
        (self.home / "user-seen.date").write_text(self.cs.dt.date.today().isoformat())
        self.report("w", "TYPE: PROGRESS\nDID: read the code\nNEXT: fix it")
        self.send("secretary", "TYPE: PROGRESS\nDID: fixed it\nNEXT: tests")  # the old way lands there too
        self.report("cur", "TYPE: PROGRESS\nDID: counted\nNEXT: table")
        self.report("sub", "TYPE: PROGRESS\nDID: for w only\nNEXT: x")  # its delegator's business
        context = self.secretary_turn('<cross-session-message from="x">hi</cross-session-message>')
        self.assertIn("[claudesec progress]", context)
        self.assertIn("don't pass it on to the user", context)
        lines = [l for l in context.splitlines() if l.startswith("- [")]
        self.assertEqual(len(lines), 3)
        self.assertIn("read the code; next: fix it", lines[0])
        self.assertTrue(lines[1].startswith("- [w ") and "fixed it" in lines[1])
        self.assertTrue(lines[2].startswith("- [cur "))
        self.assertNotIn("for w only", context)
        self.assertEqual(self.secretary_turn("hello"), "")  # taken: not given twice

    def test_progress_comes_with_the_days_first_message(self):
        self.cs.stale_items = lambda: []
        self.report("w", "TYPE: PROGRESS\nDID: halfway\nNEXT: rest")
        context = self.secretary_turn("morning")
        self.assertIn("claudesec review", context)
        self.assertIn("halfway", context)

    def test_a_long_backlog_keeps_the_latest(self):
        (self.home / "user-seen.date").write_text(self.cs.dt.date.today().isoformat())
        for i in range(self.cs.PROGRESS_KEEP + 5):
            self.report("w", f"TYPE: PROGRESS\nDID: step {i}\nNEXT: more")
        lines = [l for l in self.secretary_turn("hi").splitlines() if l.startswith("- ")]
        self.assertEqual(len(lines), self.cs.PROGRESS_KEEP + 1)
        self.assertIn("5 earlier", lines[0])
        self.assertIn(f"step {self.cs.PROGRESS_KEEP + 4}", lines[-1])

    def test_the_worker_rules_say_how_to_send_progress(self):
        self.cs.memory_settings = lambda cwd: {}
        d = self.cs.prepare_worker("w", self.cs.config(), "task", "/tmp")
        self.assertIn(f"{self.cs.hook_cmd()} report w <<'EOF'", (d / "prompt.md").read_text())

if __name__ == "__main__":
    unittest.main()
