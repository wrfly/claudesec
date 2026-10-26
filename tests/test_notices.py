"""Notices: things for the user to do that need no decision, numbered from the queue's sequence.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class Notices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "queue.md").write_text("# 决策队列\n\n## 待问（按优先级）\n\n- #51 [w] 问题（中）\n\n## 已处理\n")
        (self.home / "queue.last").write_text("51\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, saved = io.StringIO(), sys.argv
        sys.argv = ["claudesec", *argv]
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv = saved
        return out.getvalue().strip()

    def test_add_takes_the_queue_number_and_done_moves_it(self):
        self.assertEqual(self.run_cli("notice", "add", "--from", "review", "去", "review", "窗口说", "push"), "#52")
        self.assertEqual(self.run_cli("queue", "add", "[x] 另一个问题（低）"), "#53")  # one sequence
        self.assertEqual(self.run_cli("notice", "add", "装扩展"), "#54")
        self.assertEqual(self.cs.open_notices(), [(52, "review", "去 review 窗口说 push"), (54, None, "装扩展")])
        self.run_cli("notice", "done", "52")
        self.assertEqual(self.cs.open_notices(), [(54, None, "装扩展")])
        text = (self.home / "notices.md").read_text()
        done = text.split("## 已完成", 1)[1]
        self.assertIn("- #52 [review] 20", done)  # dated after the tag
        self.assertIn("#54", self.run_cli("notice", "list"))

    def test_pane_lists_open_notices(self):
        self.run_cli("notice", "add", "--from", "claudesec", "VNC 里装 Claude 扩展")
        title, blocks, _ = self.cs.notices_blocks(60)
        shown = "".join(text for _, lines, _ in blocks for line in lines for _, text in line)
        self.assertIn("VNC 里装 Claude 扩展", shown)
        self.assertIn("1", title[0][1])

    def test_show_prints_the_whole_item_from_either_file(self):
        self.run_cli("notice", "add", "--from", "c", "一条很长的通知" * 10)
        self.assertEqual(self.run_cli("show", "52"), "#52（通知 · 待办）\n[c] " + "一条很长的通知" * 10)
        self.assertEqual(self.run_cli("show", "#51"), "#51（决策队列 · 待问）\n[w] 问题（中）")
        with self.assertRaises(SystemExit):
            self.run_cli("show", "77")

    def test_done_of_a_missing_number_says_so(self):
        with self.assertRaises(SystemExit) as e:
            self.run_cli("notice", "done", "99")
        self.assertIn("no notice #99", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()


class SpendAtTheBottom(Notices):
    def test_todays_spend_is_pinned_to_the_last_row_of_the_notices_pane(self):
        self.cs.spend_today_line = lambda: "今天花费：秘书 $1.0 · 全部 $3.0"
        for with_items in (False, True):
            if with_items:
                self.run_cli("notice", "add", "装扩展")
            title, blocks, footer = self.cs.notices_blocks(80)
            body, _ = self.cs.unfold(blocks, set())
            lines, _, rowmap = self.cs.dash_view(title, body, 20, 0, footer)
            self.assertEqual(len(lines), 20)
            self.assertEqual("".join(t for _, t in lines[-1]), "今天花费：秘书 $1.0 · 全部 $3.0")
            self.assertIsNone(rowmap[-1])  # a click there opens nothing
        self.cs.spend_today_line = lambda: ""
        self.assertEqual(self.cs.notices_blocks(80)[2], [])

    def test_the_footer_stays_when_the_notices_scroll(self):
        self.cs.spend_today_line = lambda: "今天花费：秘书 $1.0 · 全部 $3.0"
        for i in range(30):
            self.run_cli("notice", "add", f"第 {i} 件")
        title, blocks, footer = self.cs.notices_blocks(80)
        body, _ = self.cs.unfold(blocks, set())
        lines, _, _ = self.cs.dash_view(title, body, 20, 0, footer)
        texts = ["".join(t for _, t in line) for line in lines]
        self.assertEqual(len(texts), 20)
        self.assertTrue(texts[-1].startswith("今天花费"))
        self.assertIn("↓", texts[-2])  # the "more below" note sits just above it
