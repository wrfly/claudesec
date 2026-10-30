"""With "language": "en", what claudesec tells the models is English too: first messages, Stop hook
reasons, the secretary's session-start context, alerts. The prompts are Chinese in either language
(the user, 2026-10-27: models follow them whatever the language). And it reads
English files and answers ("## Done", "User decision: …") as it reads Chinese ones.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_external_sessions import load

CJK = re.compile(r"[　-〿一-鿿＀-￯]")


class English(unittest.TestCase):
    LANG = "en"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": self.LANG, "cursor": False}))
        self.cs.lang.cache_clear()
        self.cs.claude_sessions = lambda: []
        self.cs.secretary_alive = lambda: True
        self.cs.tmux = lambda *a, **k: ""
        self.alerts = []
        self.cs.raise_alert = self.alerts.append
        self.saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home / "claude")

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved
        self.cs.lang.cache_clear()
        self.tmp.cleanup()

    def no_chinese(self, text, what):
        bad = [l for l in text.splitlines() if CJK.search(l)]
        self.assertEqual(bad, [], what)

    def run_hook(self, fn, *args, stdin=None):
        out, old = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps(stdin or {}))
        try:
            with contextlib.redirect_stdout(out):
                fn(*args)
        finally:
            sys.stdin = old
        return out.getvalue()

    def test_prompts(self):
        prompts = self.cs.PROMPTS
        self.assertEqual(self.cs.secretary_prompt().read_text(),
                         self.cs.render_prompt((prompts / "secretary.md").read_text()))
        for agent in ("claude", "cursor"):
            d = self.cs.prepare_worker("w", {"secretary_name": "secretary"}, "hi", self.home, agent=self.cs.AGENTS[agent])
            self.assertTrue((d / "prompt.md").read_text().startswith("# 你是 ClaudeSec worker「w」"), agent)

    def test_first_messages(self):
        for text in (self.cs.ADOPT_PROMPT_EN, self.cs.WAKE_PROMPT_EN, self.cs.WAKE_REPORT_EN,
                     self.cs.WATCH_REARM_EN, self.cs.CURSOR_REPORT_EN, self.cs.LESSONS_EN):
            self.no_chinese(text, text[:30])

    def test_stop_hook_and_session_start(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "task": "t", "model": "claude-opus-5-5", "window": None}}}))
        self.cs.prepare_worker("w", {"secretary_name": "secretary"}, "hi", self.home)
        out = self.run_hook(self.cs.hook_worker_stop, "w", stdin={"session_id": "s"})
        self.assertIn("Before ending this turn", json.loads(out)["reason"])
        self.cs.external_sessions = lambda: []
        self.cs.alive_windows = lambda: set()
        (self.home / "queue.md").write_text(self.cs.queue_template())
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_queue(SimpleNamespace(action="add", words=["[w] which one? (medium)"], asked=False, later=False))
        out = self.run_hook(self.cs.hook_secretary_session_start, stdin={"session_id": "s"})
        self.assertIn("undecided", out)
        self.assertIn("#1 [w] which one?", out)
        self.no_chinese(out, "session start")

    def test_alerts(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "task": "t", "model": "m", "window": None}}}))
        self.run_hook(self.cs.hook_worker_stop_failure, "w", stdin={"error": "overloaded"})
        self.assertTrue(self.alerts and self.alerts[0].startswith("worker w's turn broke off"), self.alerts)
        for a in self.alerts:
            self.no_chinese(a, "alert")

    def test_reads_english_answers_and_files(self):
        self.assertTrue(self.cs.is_user_decision("User decision: A"))
        self.assertTrue(self.cs.is_user_decision("用户决策：A"))
        path = self.home / "lessons.md"
        self.cs.cmd_lesson(SimpleNamespace(type="route", situation="s", correction="c"))
        path.write_text(path.read_text() + "\n## Done\n\n- old one\n")
        self.cs.cmd_lesson(SimpleNamespace(type="route", situation="s2", correction="c2"))
        text = path.read_text()
        self.assertLess(text.index("s2"), text.index("## Done"))
        self.assertEqual(self.cs.open_part(path).count("\n- "), 2)
        self.no_chinese(text, "lessons.md")


class ChineseStaysAsItWas(English):
    """The Chinese side is unchanged: Chinese messages."""
    LANG = "zh"

    def test_stop_hook_and_session_start(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {}}))
        (self.home / "workers" / "w").mkdir(parents=True)
        out = self.run_hook(self.cs.hook_worker_stop, "w", stdin={"session_id": "s"})
        self.assertIn("结束本轮前必须用 SendMessage", json.loads(out)["reason"])

    def test_alerts(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "task": "t", "model": "m", "window": None}}}))
        self.run_hook(self.cs.hook_worker_stop_failure, "w", stdin={"error": "overloaded"})
        self.assertEqual(self.alerts, ["worker w 这一轮因 API 错误中断，停在提示符上，不会自己汇报：overloaded"])

    test_first_messages = test_reads_english_answers_and_files = None


if __name__ == "__main__":
    unittest.main()
