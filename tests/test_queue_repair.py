"""queue.md damaged by a paste of Read's numbered output, and the sections claudesec then
appended a second time (2026-10-25), are repaired; the 记录 section, ages, list and doctor.

    python3 -m unittest discover -s tests
"""
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_external_sessions import load

CLEAN = """# 决策队列

说明。

## 搁置

- #6 [telegram] 以后再聊。

## 待问（按优先级）

- #74 [runner] 删掉还是留着？（低）
- #77 [upreset] 时区怎么配？（中）

## 待答

- #83 [pricing] 2026-10-20 已问：余额不足怎么办？

## 已处理

- #71 [chatsub] 2026-10-24 用户答：A
"""
# what the claudesec run that came next appended, its headings unrecognized under the numbers
APPENDED = """
## 搁置

## 待问

- #93 [feature] 隐私对话模式，先只记着（低）

## 待答

## 已处理

- #100 [circleci] 2026-10-24 用户答：删掉
"""


def damaged():
    """Each line numbered by three pastes, the numbers drifting as on the real file."""
    lines = CLEAN.splitlines()
    out = [f"{i + 1}\t{i + 1}\t{i + 1 + (i > 12)}\t{l}" for i, l in enumerate(lines)]
    return "\n".join(out) + "\n" + APPENDED


class QueueRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))
        self.path = self.home / "queue.md"

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self, action, *words, **kw):
        args = SimpleNamespace(action=action, words=list(words), asked=False, later=False, idea=False,
                               number=words[0] if words else None, to=None, note=None, older=None, fix=False)
        for k, v in kw.items():
            setattr(args, k, v)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.cs.cmd_queue(args)
        return out.getvalue().strip(), err.getvalue().strip()

    def numbers(self):
        return [(s, n) for s, n, *_ in self.cs.queue_items(self.path.read_text())]

    def test_repair_strips_numbers_and_merges_sections(self):
        lines, problems = self.cs.repair_queue(damaged().splitlines())
        self.assertEqual(len(problems), 2, problems)
        heads = [l for l in lines if l.startswith("## ")]
        self.assertEqual(heads, ["## 搁置", "## 待问（按优先级）", "## 待答", "## 已处理"])
        text = "\n".join(lines)
        self.assertNotIn("\t", text)
        self.assertLess(text.index("#77"), text.index("#93"))   # appended item after the old ones
        self.assertLess(text.index("#100"), text.index("#71"))  # 已处理: newest first

    def test_readers_see_through_the_damage(self):
        self.path.write_text(damaged())
        self.assertEqual(self.numbers(), [("backlog", 6), ("todo", 74), ("todo", 77), ("todo", 93), ("doing", 83)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cs.cmd_show(SimpleNamespace(number="77"))
        self.assertIn("时区怎么配", out.getvalue())

    def test_a_command_repairs_the_file_and_keeps_the_damaged_one(self):
        self.path.write_text(damaged())
        out, err = self.queue("move", "77", to="done", note="用户答：A")
        self.assertEqual(out, "#77 待问 → 已处理")
        self.assertIn("queue.md 坏了", err)
        backups = list((self.home / "backups").glob("queue-*.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), damaged() if damaged().endswith("\n") else damaged() + "\n")
        text = self.path.read_text()
        self.assertEqual(text.count("## 待问"), 1)
        self.assertNotIn("\t", text)
        self.assertEqual(self.queue("doctor")[0], "queue.md 没问题。")

    def test_move_of_a_missing_number_fails_loudly(self):
        self.path.write_text(CLEAN)
        with self.assertRaises(SystemExit) as e:
            self.queue("move", "999", to="done")
        self.assertIn("queue doctor", str(e.exception.code))
        (self.home / "notices.md").write_text("# 通知\n\n## 待办\n\n- #120 [w] 去登录\n")
        with self.assertRaises(SystemExit) as e:
            self.queue("move", "120", to="done")
        self.assertIn("notice done 120", str(e.exception.code))

    def test_doctor(self):
        self.path.write_text(damaged())
        with self.assertRaises(SystemExit):  # problems left: non-zero
            self.queue("doctor")
        self.assertEqual(self.path.read_text(), damaged())  # only looked
        out, _ = self.queue("doctor", fix=True)
        self.assertIn("行号", out)
        self.assertEqual(self.queue("doctor")[0], "queue.md 没问题。")
        self.path.write_text(CLEAN.replace("- #74", "- #77"))
        with self.assertRaises(SystemExit):
            self.queue("doctor")

    def test_ideas_are_not_questions(self):
        self.path.write_text(CLEAN)
        n, _ = self.queue("add", "[feature] 只记一下：隐私对话模式", idea=True)
        self.assertEqual(n, "#84")
        self.assertIn(("ideas", 84), self.numbers())
        title, blocks = self.cs.queue_blocks(40)
        self.assertIn("待问 2  待答 1", "".join(t for _, t in title))
        body = ["".join(t for _, t in l) for l in self.cs.unfold(blocks, set())[0]]
        self.assertTrue(body[-1].startswith("记录 1"), body)
        self.queue("move", "84", to="todo")
        self.assertIn(("todo", 84), self.numbers())

    def test_ages(self):
        self.path.write_text(CLEAN)
        self.queue("add", "[w] 新的")
        meta = json.loads((self.home / "queue-meta.json").read_text())
        meta["84"]["added"] = (dt.date.today() - dt.timedelta(days=9)).isoformat()
        (self.home / "queue-meta.json").write_text(json.dumps(meta))
        out, _ = self.queue("list")
        rows = {l.split()[0]: l for l in out.splitlines()[1:] if l.startswith("#")}
        self.assertIn(" 9d ", rows["#84"])
        self.assertIn(" ? ", rows["#74"])
        asked = (dt.date.today() - dt.date(2026, 10, 20)).days
        self.assertIn(f" {asked}d ", rows["#83"])  # 待答: since it was asked
        out, _ = self.queue("list", older=5)
        self.assertEqual(sorted(l.split()[0] for l in out.splitlines() if l.startswith("#")),
                         sorted(["#84"] + (["#83"] if asked >= 5 else [])))
        self.assertIn("不知道放了多久", out)
        _, blocks = self.cs.queue_blocks(44)
        line = next("".join(t for _, t in b[1][0]) for b in blocks if b[0] and b[0][1] == 84)
        self.assertTrue(line.endswith(" 9d"), line)


if __name__ == "__main__":
    unittest.main()
