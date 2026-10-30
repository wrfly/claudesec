"""A Cursor turn that ends in an error says why (the screen's "Error: Model Blocked"), and a
Cursor worker's model is switched by restarting it, not by pasting /model.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_external_sessions import load


class CursorErrors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        self.cs.lang.cache_clear()
        self.screen = ""
        self.cs.pane_text = lambda target, suggestion="mark": self.screen
        self.cs.time.sleep = lambda s: None
        self.alerts = []
        self.cs.raise_alert = self.alerts.append

    def tearDown(self):
        self.cs.lang.cache_clear()
        self.tmp.cleanup()

    def test_error_text_off_the_screen(self):
        self.screen = "  ⬢ Thinking\n\n  Error: Model Blocked\n  This model is disabled by your team admin\n\n→ Plan, search, build"
        self.assertEqual(self.cs.cursor_error_text("@1"), "Error: Model Blocked")
        self.screen = "all quiet"
        self.assertEqual(self.cs.cursor_error_text("@1"), "")
        self.assertEqual(self.cs.cursor_error_text(None), "")

    def test_the_alert_carries_it(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "up": {"agent": "cursor", "status": "PROGRESS", "window": "@1", "task": "t", "model": "m"}}}))
        (self.home / "workers" / "up").mkdir(parents=True)
        self.screen = "Error: Model Blocked"
        self.cs.hooked_worker = lambda: "up"
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps({"status": "error", "model": "grok-4.7-high"}))
        try:
            self.cs.hook_agent_stop("cursor")
        finally:
            sys.stdin = old
        w = self.cs.read_registry()[1]["up"]
        self.assertEqual(w["pending_ask"], "Cursor (grok-4.7-high): Error: Model Blocked")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Error: Model Blocked", self.alerts[0])
        self.assertIn("claudesec model up <model>", self.alerts[0])

    def test_send_refuses_a_pasted_model_switch(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "up": {"agent": "cursor", "status": "DONE", "window": "@1", "task": "t", "model": "m"}}}))
        self.cs.agent_running = lambda w: True
        old = sys.stdin
        sys.stdin = io.StringIO("/model gpt-5.6-sol-high\n")
        try:
            with self.assertRaises(SystemExit) as e:
                self.cs.cmd_send(SimpleNamespace(name="up"))
        finally:
            sys.stdin = old
        self.assertIn("claudesec model up <model>", str(e.exception.code))

    def test_model_switch_is_shelve_then_wake(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "up": {"agent": "cursor", "status": "API_ERROR", "window": "@1", "task": "t", "model": "grok"}}}))
        calls = []

        def shelve(args):
            calls.append(("shelve", args.name, args.force))
            with self.cs.registry() as reg:
                reg["workers"]["up"]["status"] = "SHELVED"

        self.cs.cmd_shelve = shelve
        self.cs.cmd_wake = lambda args: calls.append(("wake", args.name, args.model, args.quiet))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cs.cmd_model(SimpleNamespace(name="up", model="gpt-5.6-sol-high", force=False))
        self.assertEqual(calls, [("shelve", "up", False), ("wake", "up", "gpt-5.6-sol-high", True)])
        self.assertIn("now runs gpt-5.6-sol-high", out.getvalue())


if __name__ == "__main__":
    unittest.main()
