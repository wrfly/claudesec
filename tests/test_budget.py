"""The budget window and a worker's own budget: wrap-up notice at 85%, frozen at 100%, no new
work while frozen, and the stopped workers told to carry on once it opens again.

    python3 -m unittest discover -s tests
"""
import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class Budget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "dir": "/tmp", "task": "t", "session": "s", "window": "@1"}}}))
        self.spent = 0.0
        self.cs.window_spent = lambda b: self.spent
        self.cs.worker_cost = lambda workers, **kw: {n: self.spent for n in workers}
        self.cs.secretary_alive = lambda: False
        self.now = dt.datetime.now().astimezone()

    def tearDown(self):
        self.tmp.cleanup()

    def window(self, start_mins_ago, until_mins_ahead, usd=None, **extra):
        self.cs.write_budget({"start": (self.now - dt.timedelta(minutes=start_mins_ago)).isoformat(),
                              "until": (self.now + dt.timedelta(minutes=until_mins_ahead)).isoformat(),
                              "usd": usd, "frozen": False, **extra})

    def hook(self, tool="Bash"):
        out, saved = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps({"tool_name": tool}))
        try:
            with contextlib.redirect_stdout(out):
                self.cs.hook_worker_budget("w")
        finally:
            sys.stdin = saved
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def test_ok_soft_over_by_time_and_by_money(self):
        self.window(50, 50, usd=100)
        self.assertEqual(self.cs.budget_state(self.cs.read_budget())[0], "ok")
        self.window(90, 10)  # 10% of the time left
        self.assertEqual(self.cs.budget_state(self.cs.read_budget())[0], "soft")
        self.window(10, 90, usd=100)
        self.spent = 90
        self.assertEqual(self.cs.budget_state(self.cs.read_budget())[0], "soft")
        self.spent = 100
        self.assertEqual(self.cs.budget_state(self.cs.read_budget())[0], "over")
        self.spent = 0
        self.window(60, -1)  # its end has passed
        self.assertEqual(self.cs.budget_state(self.cs.read_budget())[0], "over")

    def test_wrap_up_notice_once_then_refusal_and_freeze(self):
        self.window(90, 10)
        first = self.hook()
        self.assertIn("Wrap up now", first["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.hook())  # once per window
        b = self.cs.read_budget()
        b["until"] = (self.now - dt.timedelta(minutes=1)).isoformat()
        self.cs.write_budget(b)
        denied = self.hook()["hookSpecificOutput"]
        self.assertEqual(denied["permissionDecision"], "deny")
        self.assertIn("BLOCKED", denied["permissionDecisionReason"])
        self.assertTrue(self.cs.read_budget()["frozen"])
        self.assertIsNone(self.hook("SendMessage"))  # the report goes through
        with self.assertRaises(SystemExit) as e:
            self.cs.refuse_while_frozen("spawn x")
        self.assertIn("frozen", str(e.exception.code))

    def test_extending_unfreezes_and_tells_the_stopped_worker(self):
        self.window(60, -1)
        self.hook()  # stopped
        told = []
        self.cs.send_text = lambda name, w, text, by="secretary": told.append((name, by, text))
        self.cs.alive_windows = lambda: {"@1"}
        out, saved = io.StringIO(), sys.argv
        sys.argv = ["claudesec", "budget", "--until", "+2h"]
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv = saved
        self.assertFalse(self.cs.read_budget()["frozen"])
        self.assertEqual([(n, by) for n, by, _ in told], [("w", "claudesec")])
        self.assertIn("Carry on", told[0][2])
        self.cs.refuse_while_frozen("spawn x")  # no longer refused

    def test_a_workers_own_budget(self):
        reg = json.loads((self.home / "registry.json").read_text())
        reg["workers"]["w"]["budget_usd"] = 10
        (self.home / "registry.json").write_text(json.dumps(reg))
        self.spent = 8.6
        self.assertIn("adds to the budget", self.hook()["hookSpecificOutput"]["additionalContext"])
        self.spent = 10.2
        reason = self.hook()["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("used up", reason)
        self.assertIn("whether the user adds", reason)
        self.assertIsNone(self.cs.read_budget())  # a worker's budget never freezes the window

    def test_a_booked_window_opens_through_the_reminder(self):
        self.window(60, -1, frozen=True, frozen_why="ended")
        (self.cs.worker_dir("w") / "frozen-window").write_text("x")
        told = []
        self.cs.send_text = lambda name, w, text, by="secretary": told.append(name)
        self.cs.alive_windows = lambda: {"@1"}
        self.assertEqual(self.cs.deliver_reminder({"id": "r1", "action": "budget-open", "due": self.now.isoformat(),
                                                   "target": "secretary", "text": "x"}), "budget window opened")
        self.assertFalse(self.cs.read_budget()["frozen"])
        self.assertEqual(told, ["w"])


if __name__ == "__main__":
    unittest.main()


class WorkerBudgetCommands(Budget):
    """spawn --budget refuses Cursor; budget --worker raises it and lets a stopped worker go on."""

    def run_cli(self, *argv, stdin=""):
        out, saved_argv, saved_stdin = io.StringIO(), sys.argv, sys.stdin
        sys.argv, sys.stdin = ["claudesec", *argv], io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        return out.getvalue()

    def test_budget_is_for_claude_workers_only(self):
        with self.assertRaises(SystemExit) as e:
            self.run_cli("spawn", "c", "--agent", "cursor", "--budget", "5", stdin="t")
        self.assertIn("only for Claude Code workers", str(e.exception.code))
        with self.assertRaises(SystemExit) as e:
            self.run_cli("delegate", "w", "--name", "c", "--budget", "5", stdin="t")
        self.assertIn("NOT DELEGATED", str(e.exception.code))

    def test_raising_a_budget_lets_the_stopped_worker_go_on(self):
        reg = json.loads((self.home / "registry.json").read_text())
        reg["workers"]["w"]["budget_usd"] = 10
        (self.home / "registry.json").write_text(json.dumps(reg))
        self.cs.set_worker_budget_marker("w", 10)
        self.spent = 10.5
        self.hook()  # stopped
        self.assertTrue((self.cs.worker_dir("w") / "frozen-own").exists())
        told = []
        self.cs.send_text = lambda name, w, text, by="secretary": told.append((name, by))
        self.cs.alive_windows = lambda: {"@1"}
        out = self.run_cli("budget", "--worker", "w", "--add", "5")
        self.assertIn("$15.0", out)
        self.assertEqual(json.loads((self.home / "registry.json").read_text())["workers"]["w"]["budget_usd"], 15)
        self.assertEqual(told, [("w", "claudesec")])
        self.assertFalse((self.cs.worker_dir("w") / "frozen-own").exists())
        self.run_cli("budget", "--worker", "w", "--off")
        self.assertFalse((self.cs.worker_dir("w") / "budget_usd").exists())
