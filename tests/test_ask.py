"""The secretary's choice boxes (AskUserQuestion): allowed only in a turn the user started, held
back once until the user has been told how a box behaves, and queue.md kept in step with what is
asked and answered.

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


def question(header, text, labels, multi=False):
    return {"header": header, "question": text, "multiSelect": multi,
            "options": [{"label": l, "description": ""} for l in labels]}


class Ask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "queue.md").write_text(self.cs.queue_template())
        self.nums = [self.queue("add", "[w] 现在开 PR 吗？"), self.queue("add", "[w] 两件事")]

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            move = argv[0] == "move"
            self.cs.cmd_queue(self.cs.argparse.Namespace(
                action=argv[0], words=[] if move else list(argv[1:]), asked=False, later=False, idea=False,
                number=argv[1] if move else None, to=argv[2] if move else None, note=None))
        return out.getvalue().strip().lstrip("#")

    def hook(self, fn, data):
        saved, out = sys.stdin, io.StringIO()
        sys.stdin = io.StringIO(json.dumps(data))
        try:
            with contextlib.redirect_stdout(out):
                fn()
        finally:
            sys.stdin = saved
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def user_says(self, text="12 A"):
        self.hook(self.cs.hook_secretary_prompt, {"prompt": text})

    def ask(self, questions):
        return self.hook(self.cs.hook_secretary_ask, {"tool_name": "AskUserQuestion", "tool_input": {"questions": questions}})

    def answered(self, questions, answers, annotations=None):
        return self.hook(self.cs.hook_secretary_answered, {"tool_name": "AskUserQuestion", "tool_input": {
            "questions": questions, "answers": answers, **({"annotations": annotations} if annotations else {})}})

    def told(self):
        (self.home / "ask-told").write_text("x")

    def section(self, n):
        return self.cs.queue_item_section(n)

    def denied(self, out):
        return bool(out) and out["hookSpecificOutput"].get("permissionDecision") == "deny"

    def test_a_turn_the_user_started_asks_and_moves_the_item_to_waiting(self):
        self.told()
        self.user_says()
        out = self.ask([question(f"#{self.nums[0]}", "现在开 PR 吗？", ["开（推荐）", "不开"])])
        self.assertIsNone(out)
        self.assertEqual(self.section(self.nums[0]), "doing")
        self.assertEqual(self.section(self.nums[1]), "todo")

    def test_a_turn_woken_by_a_report_or_after_the_users_turn_ended_is_refused(self):
        self.told()
        self.hook(self.cs.hook_secretary_prompt, {"prompt": '<cross-session-message from="w">TYPE: DONE'})
        self.assertTrue(self.denied(self.ask([question(f"#{self.nums[0]}", "q", ["a", "b"])])))
        self.user_says()
        self.hook(self.cs.hook_secretary_stop, {})
        out = self.ask([question(f"#{self.nums[0]}", "q", ["a", "b"])])
        self.assertTrue(self.denied(out))
        self.assertIn("queue add", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.section(self.nums[0]), "todo")

    def test_the_first_box_is_held_back_once_for_the_user_to_be_told(self):
        self.user_says()
        out = self.ask([question(f"#{self.nums[0]}", "q", ["a", "b"])])
        self.assertTrue(self.denied(out))
        self.assertIn("Esc", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.section(self.nums[0]), "todo")
        self.assertIsNone(self.ask([question(f"#{self.nums[0]}", "q", ["a", "b"])]))

    def test_options_chosen_for_every_question_move_the_item_to_done(self):
        n = self.nums[1]
        qs = [question(f"#{n}-1", "第一件？", ["A", "B"]), question(f"#{n}-2", "第二件？", ["X", "Y", "Z"], multi=True)]
        out = self.answered(qs, {"第一件？": "B", "第二件？": "X, Z"})
        self.assertEqual(self.section(n), "done")
        self.assertIn(f"#{n}", out["hookSpecificOutput"]["additionalContext"])
        done = (self.home / "queue.md").read_text().split("## 已处理")[1]
        self.assertIn("用户答：", done)
        self.assertIn("B", done)
        self.assertIn("X, Z", done)

    def test_typed_text_a_note_or_a_missing_answer_leaves_it_waiting(self):
        a, b = self.nums
        self.queue("move", a, "doing")
        qs = [question(f"#{a}", "现在开 PR 吗？", ["开", "不开"]), question(f"#{b}", "两件事？", ["A", "B"])]
        out = self.answered(qs, {"现在开 PR 吗？": "等我看看 diff"})
        self.assertEqual(self.section(a), "doing")
        self.assertEqual(self.section(b), "todo")
        self.assertIn("还在待答", out["hookSpecificOutput"]["additionalContext"])
        self.answered([qs[0]], {"现在开 PR 吗？": "开"}, {"现在开 PR 吗？": {"notes": "先 rebase"}})
        self.assertEqual(self.section(a), "doing")


if __name__ == "__main__":
    unittest.main()
