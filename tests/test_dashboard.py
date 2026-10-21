"""The queue and progress panes beside the secretary: layout, line breaking, scrolling.

    python3 -m unittest discover -s tests
"""
import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load

QUEUE = """# 决策队列

## 待问（按优先级）

0d. [web] landing-private 提交开 PR？A 现在提交（推荐）/B 等闲鱼链接一起。（中）
1. [web] 上游「Claude Max (local)」(id local-check-claude)，默认组和 test 组各分 50%，要不要删？（低，可选）
5b. [sshpw] PermitRootLogin 现为 yes，要不要改？（高）
6. [telegram] 接 Telegram，用户说以后再聊。

## 已问未答

- [projmerge] {asked} 已问：接管原生 claude 到什么程度：A/B/C。

## 已处理

- [old] 早就定了。
"""


def plain(segs):
    return "".join(text for _, text in segs)


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.calls = []
        self.cs.tmux = self.fake_tmux
        self.window_width = 126
        self.panes = [("%1", "secretary", "0")]

    def tearDown(self):
        self.tmp.cleanup()

    def fake_tmux(self, *args, check=True):
        self.calls.append(args)
        if args[0] == "list-panes":
            return "".join(f"{p}|{role}|{i}\n" for p, role, i in self.panes)
        if args[0] == "display":
            return f"{self.window_width}\n"
        if args[0] == "split-window":
            return f"%{len(self.calls) + 10}\n"
        if args[0] == "list-windows":
            return "@1\n@2\n"
        return ""

    def roles_made(self):
        return [a[a.index("@claudesec-role") + 1] for a in self.calls
                if a[0] == "set-option" and "@claudesec-role" in a and a[a.index("-t") + 1] != "%1"]

    # ---- layout

    def test_two_panes_whenever_the_window_is_wide_enough(self):
        for cols in (110, 126, 200):
            self.calls, self.window_width = [], cols
            self.cs.arrange_dashboard("claudesec")
            self.assertEqual(self.roles_made(), ["queue", "status"], cols)

    def test_none_in_a_narrow_window(self):
        self.window_width = 109
        self.cs.arrange_dashboard("claudesec")
        self.assertFalse([a for a in self.calls if a[0] == "split-window"])

    def test_the_old_combined_pane_is_replaced(self):
        self.panes.append(("%5", "both", "1"))
        self.cs.arrange_dashboard("claudesec")
        self.assertIn(("kill-pane", "-t", "%5"), self.calls)
        self.assertEqual(self.roles_made(), ["queue", "status"])

    def test_nothing_changes_when_both_panes_are_there(self):
        self.panes += [("%5", "queue", "1"), ("%6", "status", "2")]
        self.cs.arrange_dashboard("claudesec")
        self.assertFalse([a for a in self.calls if a[0] in ("kill-pane", "split-window")])

    # ---- line breaking

    def test_wrap_keeps_latin_words_whole(self):
        text = "上游「Claude Max (local)」(id local-check-claude)，默认组和 test 组各分 50%"
        for cols in (22, 24, 30):
            lines = [plain(l) for l in self.cs.wrap([("", text)], cols)]
            for word in ("Claude", "(local)」(id", "local-check-claude)，", "test", "50%"):
                self.assertTrue(any(word in l for l in lines), (cols, word, lines))
            self.assertTrue(all(self.cs.width(l) <= cols for l in lines), lines)

    def test_wrap_keeps_closing_marks_off_a_line_start(self):
        for cols in range(6, 30):
            lines = [plain(l) for l in self.cs.wrap([("", "要不要删，现在就删。好的（低）")], cols)]
            self.assertFalse([l for l in lines[1:] if l[:1] in "，。）"], (cols, lines))
            self.assertFalse([l for l in lines if l[-1:] == "（"], (cols, lines))

    def test_wrap_cuts_a_word_longer_than_a_line(self):
        lines = [plain(l) for l in self.cs.wrap([("", "x " + "a" * 25)], 10, 8)]
        self.assertEqual(lines, ["x", "aaaaaaaa", "aaaaaaaa", "aaaaaaaa", "a"])

    # ---- the queue pane

    def write_queue(self):
        asked = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        (self.home / "queue.md").write_text(QUEUE.replace("{asked}", asked))

    def test_queue_items_with_any_numbering(self):
        self.write_queue()
        items = self.cs.queue_items((self.home / "queue.md").read_text())
        self.assertEqual([(s, tag) for s, tag, _, _ in items],
                         [("ask", "web"), ("ask", "web"), ("ask", "sshpw"), ("ask", "telegram"),
                          ("asked", "projmerge")])
        self.assertEqual([note for *_, note in items][:4], ["中", "低，可选", "高", None])
        self.assertTrue(items[0][2].endswith("等闲鱼链接一起。"))

    def test_queue_pane(self):
        self.write_queue()
        title, body = self.cs.queue_lines(40)
        self.assertIn("待问 4 · 已问未答 1", plain(title))
        self.assertEqual(self.cs.width(plain(title)), 40)
        text = [plain(l) for l in body]
        self.assertTrue(all(self.cs.width(l) <= 40 for l in text), text)
        self.assertFalse([l for l in text if "早就定了" in l])
        firsts = {plain(l): l for l in body if l and l[0][1] in "▪▫"}
        colors = [l[0][0] for l in firsts.values()]
        self.assertEqual(colors, [self.cs.YELLOW, self.cs.DIM, self.cs.RED, "", self.cs.DIM])
        self.assertTrue(any(l.endswith("（2 天前问的）") for l in text), text)

    def test_empty_queue(self):
        title, body = self.cs.queue_lines(40)
        self.assertIn("待问 0", plain(title))
        self.assertEqual([plain(l) for l in body], ["没有要你定的事。"])

    # ---- the progress pane

    def test_progress_pane_lists_what_needs_the_user_first(self):
        now = dt.datetime.now().astimezone()
        at = lambda mins: (now - dt.timedelta(minutes=mins)).isoformat()
        workers = {
            "done": {"status": "DONE", "task": "t", "last_summary": "finished", "window": "@1",
                     "last_report_at": at(1)},
            "busy": {"status": "PROGRESS", "task": "t", "last_summary": "working", "window": "@9",
                     "last_report_at": at(5)},
            "asks": {"status": "NEED_DECISION", "task": "t", "last_summary": "s",
                     "pending_ask": "A or B?", "window": "@2", "last_report_at": at(90)},
            "talk": {"status": "PROGRESS", "mode": "discuss", "task": "t", "window": "@2"},
            "broke": {"status": "API_ERROR", "task": "t", "pending_ask": "529", "window": "@1",
                      "last_report_at": at(3000)},
            "zz": {"status": "SHELVED", "task": "t"},
        }
        (self.home / "registry.json").write_text(json.dumps({"focus": "busy", "workers": workers}))
        title, body = self.cs.status_lines(44)
        self.assertEqual(plain(title).rstrip(), " 等你 1 · 出错 1 · 在做 2 · 做完 1 · 搁置 1")
        self.assertEqual(self.cs.width(plain(title)), 44)
        self.assertEqual(plain(self.cs.status_lines(60)[0]).split()[0], "任务进展")
        heads = [plain(l) for l in body[0:10:2]]
        self.assertEqual([h.split()[1] for h in heads], ["asks", "broke", "busy", "talk", "done"])
        self.assertTrue(heads[0].endswith("1h") and heads[1].endswith("2d"), heads)
        self.assertIn("窗口关了", heads[2])
        self.assertIn("讨论中", heads[3])
        self.assertEqual(plain(body[1]).strip(), "A or B?")
        self.assertTrue(all(self.cs.width(plain(l)) <= 44 for l in body))

    # ---- scrolling

    def test_view_fits_and_scrolls(self):
        title, body = [("", "T")], [[("", str(i))] for i in range(30)]
        lines, top = self.cs.dash_view(title, body, 10, 0)
        self.assertEqual(len(lines), 10)
        self.assertEqual([plain(l) for l in lines[1:3]], ["0", "1"])
        self.assertIn("下面还有 22 行", plain(lines[-1]))
        lines, top = self.cs.dash_view(title, body, 10, 999)
        self.assertEqual(top, 22)
        self.assertIn("上面还有 22 行", plain(lines[1]))
        self.assertEqual(plain(lines[-1]), "29")
        self.assertEqual(len(lines), 10)
        lines, top = self.cs.dash_view(title, body[:5], 10, 7)
        self.assertEqual((len(lines), top), (6, 0))

    def test_scroll_input(self):
        sb = self.cs.scroll_by
        self.assertEqual(sb(b"\x1b[<65;10;5M\x1b[<65;10;5M", 8), 6)   # wheel down twice
        self.assertEqual(sb(b"\x1b[<64;10;5M", 8), -3)                # wheel up
        self.assertEqual(sb(b"\x1b[<68;10;5M", 8), -3)                # with shift
        self.assertEqual(sb(b"\x1b[<0;10;5M\x1b[<0;10;5m", 8), 0)     # a click
        self.assertEqual(sb(b"\x1b[B\x1b[Bjk\x1b[A", 8), 1)
        self.assertEqual(sb(b"\x1b[6~", 8), 8)
        self.assertLess(sb(b"g", 8), -1000)
        self.assertGreater(sb(b"\x1b[F", 8), 1000)


if __name__ == "__main__":
    unittest.main()
