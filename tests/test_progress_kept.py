"""A worker's PROGRESS is recorded and not passed on to the secretary: delivered, it showed up in
the secretary's window, and Claude Code pushed the silent secretary to say something about it.

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
            "w": {"status": "STARTING", "dir": "/tmp", "task": "t", "session": "s", "window": "@1"}}}))
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
        self.assertIn("Not an error", got["permissionDecisionReason"])
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

    def test_cursor_progress_raises_no_alert(self):
        report = lambda text: self.run_hook(self.cs.cmd_report, text, types.SimpleNamespace(name="w"))
        self.assertIn("recorded", report("TYPE: PROGRESS\nDID: halfway\nNEXT: rest"))
        self.assertEqual(self.alerts, [])
        self.assertEqual(self.cs.read_registry()[1]["w"]["status"], "PROGRESS")
        self.assertIn("reported to", report("TYPE: DONE\nDID: all of it\nRESULT: ok\nDECISIONS: none"))
        self.assertEqual(len(self.alerts), 1)


if __name__ == "__main__":
    unittest.main()
