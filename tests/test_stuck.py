"""A worker stuck on a dialog wakes the secretary once; Claude Code's usage-credits question is
answered with the option that costs nothing extra, and the model swap is remembered.

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_external_sessions import load

BOX = """
● Done with the first part. The dialog said "Enter to confirm · Esc to cancel".

────────────────────────────────────────
❯
────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
"""

PERMISSION = """
● Write(c1aud3-plan-first.md)

 Overwrite file c1aud3-plan-first.md
 Do you want to overwrite c1aud3-plan-first.md?
 ❯ 1. Yes
   2. Yes, allow all edits during this session (shift+tab)
   3. No

 Esc to cancel · Tab to amend
"""

CREDITS = """
 Fable 5.1 now uses usage credits

 Fable 5.1 runs on usage credits, purchased separately from your plan.

   Switch to Opus 5.5 and continue
 ❯ Continue with Fable 5.1

 Enter to confirm · Esc to cancel
"""


class Stuck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.workers = {
            "plans": {"status": "PROGRESS", "window": "@57", "model": "claude-fable-5-1", "mode": "discuss",
                      "session": "s1", "task": "t", "last_report_at": "x"},
            "old": {"status": "SHELVED", "window": None, "model": "claude-fable-5-1", "session": "s2", "task": "t"},
        }
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": self.workers}))
        self.screen = BOX
        self.keys, self.watching = [], False
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
            return "1 1" if self.watching else "0 1"
        if args[0] == "send-keys":
            key = args[-1]
            self.keys.append(key)
            if key in ("Up", "Down"):  # the cursor moves between the two options
                self.screen = self.screen.replace("❯ ", "  ") if "❯ Continue" in self.screen else self.screen
                self.screen = self.screen.replace("   Switch to", " ❯ Switch to").replace(" ❯ Continue", "   Continue")
            if key == "Enter":
                self.screen = BOX
        return ""

    def alerts(self):
        return sorted(p.read_text() for p in (self.home / "alerts").glob("*.txt")) if (self.home / "alerts").exists() else []

    def test_only_a_dialog_at_the_bottom_counts(self):
        self.assertIsNone(self.cs.screen_dialog(BOX))
        self.assertIn("Do you want to overwrite", self.cs.screen_dialog(PERMISSION))
        self.assertEqual(self.cs.model_id("Opus 5.5"), "claude-opus-5-5")

    def test_usage_credits_switch_is_picked_and_remembered(self):
        self.screen = CREDITS
        note = self.cs.answer_credit_dialog("plans", "@57")
        self.assertEqual(self.keys, ["Up", "Enter"])
        self.assertIn("claude-opus-5-5", note)
        reg = json.loads((self.home / "registry.json").read_text())
        self.assertEqual(reg["workers"]["plans"]["model"], "claude-opus-5-5")
        self.assertEqual(reg["workers"]["old"]["model"], "claude-opus-5-5")  # shelved on the same model
        self.assertEqual(reg["credit_models"], {"claude-fable-5-1": "claude-opus-5-5"})
        self.assertEqual(self.cs.unpaid("claude-fable-5-1"), "claude-opus-5-5")
        self.assertEqual(self.cs.unpaid("claude-sonnet-5-5"), "claude-sonnet-5-5")

    def test_no_credits_dialog_no_keys(self):
        self.screen = PERMISSION
        self.assertIsNone(self.cs.answer_credit_dialog("plans", "@57"))
        self.assertEqual(self.keys, [])

    def test_watchdog_answers_the_credits_dialog_at_once(self):
        self.screen = CREDITS
        self.assertEqual(len(self.cs.check_stuck(now_t=1000)), 1)
        self.assertEqual(self.keys[-1], "Enter")

    def test_a_dialog_left_a_minute_wakes_the_secretary_once(self):
        self.screen = PERMISSION
        self.assertEqual(self.cs.check_stuck(now_t=1000), [])
        self.assertEqual(self.cs.check_stuck(now_t=1030), [])
        raised = self.cs.check_stuck(now_t=1061)
        self.assertEqual(len(raised), 1)
        self.assertIn("Do you want to overwrite", raised[0])
        self.assertEqual(self.cs.check_stuck(now_t=1500), [])  # the same dialog: not again
        self.assertEqual(len(self.alerts()), 1)
        self.screen = BOX  # answered
        self.cs.check_stuck(now_t=1600)
        self.screen = PERMISSION  # a new one later starts its own minute
        self.assertEqual(self.cs.check_stuck(now_t=1700), [])
        self.assertEqual(len(self.cs.check_stuck(now_t=1761)), 1)

    def test_not_while_the_user_looks_at_it(self):
        self.screen, self.watching = PERMISSION, True
        self.cs.check_stuck(now_t=1000)
        self.assertEqual(self.cs.check_stuck(now_t=1100), [])
        self.watching = False  # looked away and left it
        self.assertEqual(len(self.cs.check_stuck(now_t=1110)), 1)

    def test_a_status_the_hook_already_alerted_is_not_repeated(self):
        reg = json.loads((self.home / "registry.json").read_text())
        reg["workers"]["plans"].update(status="API_ERROR", pending_ask="524", last_report_at="t1",
                                       alerted_for="API_ERROR|t1")
        (self.home / "registry.json").write_text(json.dumps(reg))
        self.cs.check_stuck(now_t=1000)
        self.assertEqual(self.cs.check_stuck(now_t=1100), [])
        reg["workers"]["plans"].pop("alerted_for")  # the hook didn't (the user was looking then)
        (self.home / "registry.json").write_text(json.dumps(reg))
        (self.home / "stuck.json").unlink()
        self.cs.check_stuck(now_t=2000)
        raised = self.cs.check_stuck(now_t=2061)
        self.assertEqual(len(raised), 1)
        self.assertIn("524", raised[0])


if __name__ == "__main__":
    unittest.main()
