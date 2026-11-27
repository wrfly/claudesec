"""The delegate → wait → report chain, from what went wrong with hosted2 on 2026-11-27/28:
a report typed "DONE. …" on one line counted as no report; a turn that died on a 402 left the
result `wait` had just handed over unread and the error logged as "unknown"; a compaction with an
empty summary left the worker with nothing to pick up from; a refused delegate was waited on.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_external_sessions import load


class DelegateChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "boss": {"status": "PROGRESS", "dir": str(self.home), "task": "t", "model": "claude-opus-5-5",
                     "window": None, "started_at": "2026-11-27T00:00:00+08:00"},
            "helper": {"status": "PROGRESS", "dir": str(self.home), "task": "t", "agent": "cursor",
                       "delegated_by": "boss", "window": None, "started_at": "2026-11-27T00:00:00+08:00"}}}))
        self.alerts = []
        self.cs.raise_alert = lambda text, key=None: self.alerts.append(text)
        self.cs.secretary_alive = lambda: True
        self.cs.user_watching = lambda window: False

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, fn, data, *args):
        out, saved = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps(data))
        try:
            with contextlib.redirect_stdout(out):
                fn(*args)
        finally:
            sys.stdin = saved
        return out.getvalue()

    def delegated(self, name="helper", **extra):
        mark = {"by": "boss", "since": time.time() - 60, "log_start": 0, "agent": "cursor", "label": "Cursor", **extra}
        (self.cs.worker_dir(name) / "delegation.json").write_text(json.dumps(mark))

    def wait(self, name="helper"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cs.cmd_wait(SimpleNamespace(name=name, timeout=0))
        return out.getvalue()

    # A
    def test_a_type_with_the_report_after_it_on_one_line_counts(self):
        for typ in ("DONE. 已做完", "DONE：已做完", "DONE, 已做完", "NEED_DECISION. 问一件事", "done"):
            self.assertIn(self.cs.report_type(self.cs.parse_report(f"TYPE: {typ}")), self.cs.TERMINAL, typ)
        self.assertEqual(self.cs.report_type({}), "UNTYPED")
        self.assertEqual(self.cs.report_type({"TYPE": "完成"}), "UNTYPED")
        self.cs.record_report("boss", "TYPE: DONE. 已做完")
        self.assertEqual(self.cs.read_registry()[1]["boss"]["status"], "DONE")
        self.assertTrue((self.cs.worker_dir("boss") / "reported").exists())

    def test_wait_takes_such_a_report_as_final(self):
        self.delegated()
        self.cs.append_log("helper", "worker→secretary", "TYPE: DONE. 都改完了")
        self.assertIn("都改完了", self.wait())

    # C
    def test_an_api_error_keeps_what_claude_code_showed(self):
        self.hook(self.cs.hook_worker_stop_failure, {"error": "unknown",
                  "last_assistant_message": "API Error: 402 上游订阅的 5 小时额度已用完"}, "boss")
        log = self.cs.read_log("boss")[-1]
        self.assertTrue(log[0].endswith("api-error"))
        self.assertIn("402", log[1])
        self.assertEqual(self.cs.api_error_text({"error": "rate_limit", "error_details": "429 slow down"}),
                         "rate_limit: 429 slow down")
        self.assertEqual(self.cs.api_error_text({}), "unknown error")

    def test_a_result_handed_over_in_the_turn_that_died_is_named(self):
        (self.cs.worker_dir("boss") / "last-stop.json").write_text("{}")
        old = time.time() - 120
        os.utime(self.cs.worker_dir("boss") / "last-stop.json", (old, old))
        self.delegated()
        self.cs.append_log("helper", "worker→secretary", "TYPE: DONE\nDID: 删掉旧入口")
        self.wait()  # hands it over, marking delivered_at
        self.hook(self.cs.hook_worker_stop_failure, {"error": "unknown", "last_assistant_message": "API Error: 402"}, "boss")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("helper DONE", self.alerts[0])
        self.assertIn("402", self.alerts[0])

    def test_a_result_read_in_a_turn_that_ended_normally_is_not(self):
        self.delegated(delivered_at=time.time() - 120)
        self.cs.append_log("helper", "worker→secretary", "TYPE: DONE\nDID: x")
        (self.cs.worker_dir("boss") / "last-stop.json").write_text("{}")
        self.hook(self.cs.hook_worker_stop_failure, {"error": "unknown"}, "boss")
        self.assertNotIn("helper", self.alerts[0])

    # B
    def test_after_a_compaction_the_worker_gets_its_task_talk_and_delegations(self):
        self.cs.append_log("boss", "secretary→worker", "请做 T21a")
        self.cs.append_log("boss", "worker→secretary", "TYPE: DONE\nDID: 派给了 Cursor")
        self.delegated()
        self.cs.append_log("helper", "worker→secretary", "TYPE: DONE\nDID: 审完\nRESULT: 报告在 /x/R1-report.md")
        self.delegated(name="other")
        out = self.hook(self.cs.hook_worker_session_start, {"session_id": "s2", "source": "compact"}, "boss")
        self.assertIn("task.md", out)
        self.assertIn("请做 T21a", out)
        self.assertIn("/x/R1-report.md", out)
        self.assertIn("log other", out)  # closed without a final report
        self.assertEqual(self.cs.read_registry()[1]["boss"]["session"], "s2")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("boss", self.alerts[0])
        self.cs.append_log("helper", "task", "再改一处")
        self.delegated(log_start=len(self.cs.read_log("helper")))
        out = self.hook(self.cs.hook_worker_session_start, {"session_id": "s3", "source": "compact"}, "boss")
        self.assertIn("wait helper", out)  # still at work on the new round

    def test_a_plain_start_or_resume_adds_nothing(self):
        for source in ("startup", "resume", None):
            self.assertEqual(self.hook(self.cs.hook_worker_session_start, {"session_id": "s", "source": source}, "boss"), "")
        self.assertEqual(self.alerts, [])

    # D1
    def test_a_refused_delegate_is_what_wait_explains(self):
        self.cs.AGENTS["cursor"].available = lambda: (False, "no cursor here")
        saved_argv, saved_stdin = sys.argv, sys.stdin
        sys.argv = ["claudesec", "delegate", "boss", "--name", "r1", "--agent", "cursor"]
        sys.stdin = io.StringIO("只读审核")
        try:
            with self.assertRaises(SystemExit) as e, contextlib.redirect_stdout(io.StringIO()):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        self.assertIn("NOT DELEGATED", str(e.exception.code))
        self.assertIn("wait r1", str(e.exception.code))
        with self.assertRaises(SystemExit) as e:
            self.wait("r1")
        self.assertIn("被拒", str(e.exception.code))
        self.assertIn("no cursor here", str(e.exception.code))

    def test_a_refusal_after_an_earlier_delegation_hides_the_old_result(self):
        self.delegated()
        self.cs.append_log("helper", "worker→secretary", "TYPE: DONE\nDID: 上一件")
        self.cs.refused_mark("helper").write_text(json.dumps({"by": "boss", "at": time.time(), "why": "full"}))
        with self.assertRaises(SystemExit) as e:
            self.wait()
        self.assertIn("full", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
