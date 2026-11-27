"""Queue and to-do number wrap-around (1..queue_max_number, default 999), retiring reused closed
entries to #N@YYYY-MM-DD, lookup disambiguation, and stale/idle/choice-box state reset.

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
from types import SimpleNamespace

from test_external_sessions import load


class QueueWrap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err, saved = io.StringIO(), io.StringIO(), sys.argv
        sys.argv = ["claudesec", *argv]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.cs.main()
        finally:
            sys.argv = saved
        return out.getvalue().strip(), err.getvalue().strip()

    def set_config(self, **kw):
        cfg = json.loads((self.home / "config.json").read_text())
        cfg.update(kw)
        (self.home / "config.json").write_text(json.dumps(cfg))

    def test_default_cap_wraps_at_999_and_skips_every_open_section(self):
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

- #1 [w] 一条记录

## 搁置

- #2 [w] 一条搁置

## 待问（按优先级）

- #3 [w] 一条待问（中）

## 待答

- #4 [w] 2026-11-20 已问：一条待答

## 已处理

- #6 [old] 2026-10-01 用户答：早先定了
- #998 [old] 2026-11-27 用户答：刚定
""")
        (self.home / "notices.md").write_text("""# 待办事项

## 待办

- #5 [w] 一条未完成待办

## 已完成

- #7 [old] 2026-10-02 去窗口说 push
""")
        (self.home / "queue.last").write_text("998\n")

        self.assertEqual(self.run_cli("queue", "add", "[a] 第 999 件")[0], "#999")
        # Wraps past 999 to 1, skipping #1(ideas), #2(backlog), #3(todo), #4(doing), #5(open notice)
        self.assertEqual(self.run_cli("queue", "add", "[b] 绕回来的第一件")[0], "#6")
        # Next advances from 6 -> 7 (reusing done notice #7), NOT jumping back to 999
        self.assertEqual(self.run_cli("notice", "add", "--from", "c", "新待办")[0], "#7")
        self.assertEqual(self.run_cli("queue", "add", "[d] 再下一件")[0], "#8")

        qtext = (self.home / "queue.md").read_text()
        ntext = (self.home / "notices.md").read_text()
        # Old closed #6 and #7 were retired to dated refs
        self.assertIn("- #6@2026-10-01 [old] 用户答：早先定了", qtext)
        self.assertNotIn("- #6 [old]", qtext)
        self.assertIn("- #7@2026-10-02 [old] 去窗口说 push", ntext)
        self.assertNotIn("- #7 [old]", ntext)
        # queue doctor is completely happy after wrap & reuse
        self.assertEqual(self.run_cli("queue", "doctor")[0], "queue.md 没问题。")

    def test_cross_file_retirement_queue_to_notice_and_notice_to_queue(self):
        self.set_config(queue_max_number=3)
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

## 待答

## 已处理

- #1 [qold] 2026-10-10 用户答：A
""")
        (self.home / "notices.md").write_text("""# 待办事项

## 待办

## 已完成

- #2 [nold] 2026-10-11 装扩展
""")
        (self.home / "queue.last").write_text("3\n")

        # Notice reuses #1 (which was closed in queue.md)
        self.assertEqual(self.run_cli("notice", "add", "--from", "new", "新通知")[0], "#1")
        # Queue reuses #2 (which was closed in notices.md)
        self.assertEqual(self.run_cli("queue", "add", "[new] 新问题（高）")[0], "#2")

        self.assertIn("- #1@2026-10-10 [qold] 用户答：A", (self.home / "queue.md").read_text())
        self.assertIn("- #2@2026-10-11 [nold] 装扩展", (self.home / "notices.md").read_text())

        # show returns the new active items, while @date reaches the retired ones
        self.assertEqual(self.run_cli("show", "1")[0], "#1（待办事项 · 待办）\n[new] 新通知")
        self.assertEqual(self.run_cli("show", "#2")[0], "#2（决策队列 · 待问）\n[new] 新问题（高）")
        self.assertEqual(self.run_cli("show", "1@2026-10-10")[0], "#1@2026-10-10（决策队列 · 已处理）\n[qold] 用户答：A")
        self.assertEqual(self.run_cli("show", "#2@2026-10-11")[0], "#2@2026-10-11（待办事项 · 已完成）\n[nold] 装扩展")

    def test_retire_without_date_uses_meta_then_old(self):
        self.set_config(queue_max_number=2)
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

## 待答

## 已处理

- #1 [m] 没写日期但有 meta
- #2 [u] 啥日期都没有
""")
        (self.home / "queue-meta.json").write_text(json.dumps({"1": {"added": "2026-09-15"}}))
        (self.home / "queue.last").write_text("2\n")

        self.assertEqual(self.run_cli("queue", "add", "[n1] 新一")[0], "#1")
        self.assertEqual(self.run_cli("queue", "add", "[n2] 新二")[0], "#2")
        qtext = (self.home / "queue.md").read_text()
        self.assertIn("- #1@2026-09-15 [m] 没写日期但有 meta", qtext)
        self.assertIn("- #2@old [u] 啥日期都没有", qtext)
        self.assertEqual(self.run_cli("show", "2@old")[0], "#2@old（决策队列 · 已处理）\n[u] 啥日期都没有")

    def test_config_zero_disables_wrapping(self):
        self.set_config(queue_max_number=0)
        (self.home / "queue.md").write_text(self.cs.queue_template())
        (self.home / "queue.last").write_text("999\n")
        self.assertEqual(self.run_cli("queue", "add", "[w] 不回绕")[0], "#1000")

    def test_overflow_when_all_numbers_in_use_and_recovery_when_freed(self):
        self.set_config(queue_max_number=2)
        self.assertEqual(self.run_cli("queue", "add", "[a] 一")[0], "#1")
        self.assertEqual(self.run_cli("queue", "add", "[b] 二")[0], "#2")
        out, err = self.run_cli("queue", "add", "[c] 三")
        self.assertEqual(out, "#3")
        self.assertIn("1–2", err)
        # Close #1, then next allocation wraps back into 1..2
        self.run_cli("queue", "move", "1", "done", "--note", "用户答：A")
        self.assertEqual(self.run_cli("queue", "add", "[d] 四")[0], "#1")

    def test_same_number_reused_twice_on_same_day(self):
        self.set_config(queue_max_number=1)
        today = dt.date.today().isoformat()
        self.assertEqual(self.run_cli("queue", "add", "[a] 第一次")[0], "#1")
        self.run_cli("queue", "move", "1", "done", "--note", "用户答：一")
        self.assertEqual(self.run_cli("queue", "add", "[b] 第二次")[0], "#1")
        self.run_cli("queue", "move", "1", "done", "--note", "用户答：二")
        self.assertEqual(self.run_cli("queue", "add", "[c] 第三次")[0], "#1")

        qtext = (self.home / "queue.md").read_text()
        self.assertEqual(qtext.count(f"- #1@{today} "), 2)
        self.assertEqual(self.run_cli("queue", "doctor")[0], "queue.md 没问题。")
        self.assertIn("第三次", self.run_cli("show", "1")[0])
        retired = self.run_cli("show", f"1@{today}")[0]
        self.assertIn("用户答：一", retired)
        self.assertIn("用户答：二", retired)

    def test_queue_number_wraps_and_retires(self):
        self.set_config(queue_max_number=2)
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

- [new] 手写没编号的

## 待答

## 已处理

- #1 [old] 2026-10-05 用户答：旧
""")
        (self.home / "queue.last").write_text("2\n")
        out, _ = self.run_cli("queue", "number")
        self.assertEqual(out, "- #1 [new] 手写没编号的")
        qtext = (self.home / "queue.md").read_text()
        self.assertIn("- #1@2026-10-05 [old] 用户答：旧", qtext)
        self.assertEqual(self.run_cli("queue", "doctor")[0], "queue.md 没问题。")

    def test_hand_edited_duplicate_prefers_open_item_everywhere(self):
        """Even if a hand edit leaves bare #5 in 已处理 alongside an open #5, open wins."""
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

## 待答

## 已处理

- #5 [old] 2026-10-01 用户答：旧队列条目
""")
        (self.home / "notices.md").write_text("""# 待办事项

## 待办

- #5 [live] 新待办

## 已完成
""")
        # show prefers open notice over closed queue entry
        self.assertEqual(self.run_cli("show", "5")[0], "#5（待办事项 · 待办）\n[live] 新待办")
        # queue move refuses to touch old done queue item when #5 is an open notice
        with self.assertRaises(SystemExit) as e:
            self.run_cli("queue", "move", "5", "done")
        self.assertIn("notice done 5", str(e.exception.code))

        # notice done closes the open notice; running it a second time refuses cleanly
        self.run_cli("notice", "done", "5")
        with self.assertRaises(SystemExit) as e:
            self.run_cli("notice", "done", "5")
        self.assertIn("已完成", str(e.exception.code))

    def test_hand_edited_open_and_done_in_queue_prefers_open_for_move_and_ask_hooks(self):
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

- #9 [live] 新问题（高）

## 待答

## 已处理

- #9 [old] 2026-10-01 用户答：旧决定
""")
        self.assertEqual(self.cs.queue_item_section("9"), "todo")
        self.assertEqual(self.run_cli("show", "9")[0], "#9（决策队列 · 待问）\n[live] 新问题（高）")

        # Choice box PreToolUse moves open #9 to doing, PostToolUse moves it to done and retires old #9
        (self.home / "ask-told").write_text("x")
        (self.home / "user-turn").write_text("now")
        q = {"header": "#9", "question": "新问题？", "multiSelect": False,
             "options": [{"label": "A", "description": ""}, {"label": "B", "description": ""}]}
        saved, out = sys.stdin, io.StringIO()
        sys.stdin = io.StringIO(json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": [q]}}))
        try:
            with contextlib.redirect_stdout(out):
                self.cs.hook_secretary_ask()
        finally:
            sys.stdin = saved
        self.assertEqual(self.cs.queue_item_section("9"), "doing")

        saved, out = sys.stdin, io.StringIO()
        sys.stdin = io.StringIO(json.dumps({"tool_name": "AskUserQuestion",
                                            "tool_input": {"questions": [q], "answers": {"新问题？": "A"}}}))
        try:
            with contextlib.redirect_stdout(out):
                self.cs.hook_secretary_answered()
        finally:
            sys.stdin = saved
        self.assertEqual(self.cs.queue_item_section("9"), "done")
        qtext = (self.home / "queue.md").read_text()
        self.assertIn("- #9@2026-10-01 [old] 用户答：旧决定", qtext)
        self.assertIn("选择框，用户答：新问题？ → A", qtext)
        self.assertEqual(self.run_cli("queue", "doctor")[0], "queue.md 没问题。")

    def test_reuse_resets_meta_stale_and_idle_state(self):
        self.set_config(queue_max_number=1)
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

## 待答

## 已处理

- #1 [old] 2020-01-01 用户答：旧
""")
        (self.home / "queue.last").write_text("1\n")
        (self.home / "queue-meta.json").write_text(json.dumps({"1": {"added": "2020-01-01", "at": "2020-01-01T00:00:00+08:00"}}))
        (self.home / "stale.json").write_text(json.dumps({"queue:1": ["closed", "age:1"], "notice:1": ["closed"]}))
        (self.home / "idle-nudges.json").write_text(json.dumps({"items": [1], "sent": 3}))

        self.assertEqual(self.run_cli("queue", "add", "[live] 新问题（中）")[0], "#1")
        meta = json.loads((self.home / "queue-meta.json").read_text())
        self.assertEqual(meta["1"]["added"], dt.date.today().isoformat())
        stale = json.loads((self.home / "stale.json").read_text())
        self.assertNotIn("queue:1", stale)
        self.assertNotIn("notice:1", stale)
        idle = json.loads((self.home / "idle-nudges.json").read_text())
        self.assertNotIn(1, idle["items"])

        # Pane shows the new #1 cleanly with no age (added today) and doesn't leak retired old #1
        _, blocks = self.cs.queue_blocks(44)
        rendered = "\n".join("".join(t for _, t in line) for _, folded, _ in blocks for line in folded)
        self.assertIn("新问题", rendered)
        self.assertNotIn("用户答：旧", rendered)

    def test_lowering_cap_wraps_from_one_and_show_falls_back_to_retired(self):
        self.set_config(queue_max_number=50)
        (self.home / "queue.md").write_text("""# 决策队列

## 记录

## 搁置

## 待问（按优先级）

## 待答

## 已处理

- #1@2026-10-01 [old] 用户答：很早以前
""")
        (self.home / "queue.last").write_text("950\n")
        # Before reuse: no active #1, so `show 1` falls back to retired #1@2026-10-01
        self.assertEqual(self.run_cli("show", "1")[0], "#1@2026-10-01（决策队列 · 已处理）\n[old] 用户答：很早以前")
        # Lowered cap: queue.last 950 >= 50 wraps straight to #1
        self.assertEqual(self.run_cli("queue", "add", "[w] 新建")[0], "#1")
        self.assertEqual(self.run_cli("show", "1")[0], "#1（决策队列 · 待问）\n[w] 新建")


if __name__ == "__main__":
    unittest.main()
