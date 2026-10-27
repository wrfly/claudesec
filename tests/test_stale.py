"""The sweep: open queue items and to-dos that may be out of date are pointed out to the secretary,
never removed (the user, 2026-10-27).

    python3 -m unittest discover -s tests
"""
import contextlib
import datetime as dt
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load

QUEUE = """# 决策队列

## 记录

- #7 [gone] 一个想法

## 搁置

- #4 [gone] 先放着的（低）

## 待问（按优先级）

- #1 [gone] 它关掉之前问的（中）
- #2 [live] 等 !12 合了再说（中）
- #3 [live] !12 和 !13 一起看（中）
- #5 [live] 很久以前的问题（低）

## 待答

## 已处理
"""


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


class Stale(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))
        # a workspace with one repository in it, where MR !12 has been merged
        work = self.home / "work"
        repo = work / "svc"
        repo.mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "commit", "-q", "--allow-empty", "-m", "start")
        git(repo, "checkout", "-q", "-b", "feat")
        git(repo, "commit", "-q", "--allow-empty", "-m", "feat")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "feat", "-m", "Merge 'feat' into 'main'\n\nSee merge request: !12")
        git(repo, "remote", "add", "origin", "git@example.com:team/svc.git")
        git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "live": {"status": "PROGRESS", "task": "t", "dir": str(work), "window": "@1"}}}))
        hist = self.home / "history" / "2026-10"
        hist.mkdir(parents=True)
        (hist / "2026-10-20-gone.md").write_text(
            self.cs.frontmatter(date="2026-10-20", project=str(work), worker="gone", session=None, status="DONE")
            + "## 任务\nt\n")
        (self.home / "queue.md").write_text(QUEUE)
        old = (dt.date.today() - dt.timedelta(days=9)).isoformat()
        (self.home / "queue-meta.json").write_text(json.dumps({"5": {"added": old}, "4": {"added": "2020-01-01"}}))
        (self.home / "notices.md").write_text("# 待办事项\n\n## 待办\n\n- #6 [gone] 去它窗口说 push\n\n## 已完成\n")

    def tearDown(self):
        self.tmp.cleanup()

    def found(self):
        return {(where, n): [key for key, _ in reasons] for where, n, _, _, reasons in self.cs.stale_items()}

    def alerts(self):
        d = self.home / "alerts"
        texts = [p.read_text() for p in sorted(d.glob("*.txt"))] if d.exists() else []
        for p in d.glob("*.txt") if d.exists() else []:
            p.unlink()
        return texts

    def test_what_can_be_told_mechanically(self):
        self.assertEqual(self.found(), {
            ("queue", 1): ["closed"],      # its worker was closed
            ("queue", 2): ["merged:12"],   # every MR it names is merged
            ("queue", 5): ["age:1"],       # waited past stale_days
            ("notice", 6): ["closed"],
        })  # #3 names an unmerged !13; backlog (#4) and ideas (#7) are left alone

    def test_the_secretary_is_told_once_and_nothing_is_removed(self):
        self.cs.check_stale()
        (alert,) = self.alerts()
        self.assertTrue(alert.startswith("[巡检]"))
        for line in ("决策队列 #1 [gone]", "已关闭（2026-10-20 归档）", "!12 都已合入（svc）", "放了 9 天",
                     "待办事项 #6 [gone]", "claudesec queue move 编号 done"):
            self.assertIn(line, alert)
        self.assertEqual((self.home / "queue.md").read_text(), QUEUE)
        self.cs.check_stale()
        self.assertEqual(self.alerts(), [])  # the same reasons aren't repeated
        old = (dt.date.today() - dt.timedelta(days=14)).isoformat()
        (self.home / "queue-meta.json").write_text(json.dumps({"5": {"added": old}}))
        self.cs.check_stale()
        (alert,) = self.alerts()
        self.assertIn("#5", alert)  # another stale_days on: said again
        self.assertNotIn("#1", alert)

    def test_an_item_gone_from_the_files_is_forgotten(self):
        self.cs.check_stale()
        self.alerts()
        (self.home / "notices.md").write_text("# 待办事项\n\n## 待办\n\n## 已完成\n")
        self.cs.check_stale()
        self.assertNotIn("notice:6", json.loads((self.home / "stale.json").read_text()))

    def test_stale_days_zero_turns_the_age_off(self):
        (self.home / "config.json").write_text(json.dumps({"language": "zh", "stale_days": 0}))
        self.assertNotIn(("queue", 5), self.found())

    def prompt(self, text):
        out, saved = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps({"prompt": text}))
        try:
            with contextlib.redirect_stdout(out):
                self.cs.hook_secretary_prompt()
        finally:
            sys.stdin = saved
        return out.getvalue()

    def test_the_first_message_of_the_day_brings_them_all(self):
        context = json.loads(self.prompt("早"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("claudesec review", context)
        self.assertIn("[巡检]", context)
        self.assertIn("决策队列 #2 [live]", context)
        self.assertEqual(self.prompt("再说一句"), "")  # once a day
        self.cs.check_stale()
        self.assertEqual(self.alerts(), [])  # already told

    def test_a_done_or_a_close_has_the_sweep_look_soon(self):
        (self.home / "workers" / "live").mkdir(parents=True)
        self.cs.record_report("live", "TYPE: PROGRESS\nDID: x\nNEXT: y")
        self.assertFalse((self.home / "stale-due").exists())
        self.cs.record_report("live", "TYPE: DONE\nDID: x\nRESULT: y\nDECISIONS: 无")
        self.assertTrue((self.home / "stale-due").exists())
        (self.home / "stale-due").unlink()
        self.cs.tmux = lambda *a, **k: ""
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_close(type("Args", (), {"name": "live"})())
        self.assertTrue((self.home / "stale-due").exists())

    def test_a_to_do_records_when_it_was_added(self):
        saved = sys.argv
        sys.argv = ["claudesec", "notice", "add", "装扩展"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.cs.main()
        finally:
            sys.argv = saved
        meta = json.loads((self.home / "queue-meta.json").read_text())
        self.assertEqual(meta["8"]["added"], dt.date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
