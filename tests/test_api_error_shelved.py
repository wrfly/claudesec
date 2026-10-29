"""A worker on an API error (or a permission dialog) when the secretary exits is shelved and woken
back to the status it had before, not to an API_ERROR whose message shelving cleared, which made
`claudesec status` crash on a None summary (modimpl, convpack, 2026-10-30).

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from test_external_sessions import load


class ApiErrorShelved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))
        self.write({"status": "API_ERROR", "status_before": "DONE", "pending_ask": "401 The credential is invalid",
                    "task": "t", "dir": str(self.home), "session": "sid", "window": None,
                    "model": "claude-opus-5-5", "last_summary": "s", "started_at": "2026-10-30T00:00:00+08:00"})
        (self.home / "workers" / "w").mkdir(parents=True)
        self.cs.conversation_exists = lambda w: True
        self.cs.live_holder = lambda sid: None
        self.cs.tmux = lambda *a, **k: ""

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, w):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {"w": w}}))

    def worker(self):
        return self.cs.read_registry()[1]["w"]

    def test_shelve_keeps_the_status_before_the_error(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_shelve(SimpleNamespace(name="w", note="n", as_name=None, force=True, by="exit"))
        w = self.worker()
        self.assertEqual((w["status"], w["status_before"], w["pending_ask"]), ("SHELVED", "DONE", None))

    def test_shelve_all_marks_it_the_same_way(self):
        self.cs.cmd_wake = lambda args: None
        self.cs.wake_orphans()
        w = self.worker()
        self.assertEqual((w["status"], w["status_before"], w["pending_ask"]), ("SHELVED", "DONE", None))

    def test_a_status_left_by_an_older_version_heals(self):
        self.assertEqual(self.cs.settled("API_ERROR"), "PROGRESS")
        self.assertEqual(self.cs.settled("WAITING_PERMISSION"), "PROGRESS")
        self.assertEqual(self.cs.settled(None), "PROGRESS")
        self.assertEqual(self.cs.settled("BLOCKED"), "BLOCKED")

    def test_status_does_not_crash_on_an_error_with_nothing_to_say(self):
        self.cs.alive_windows = lambda: set()
        self.cs.worker_usage = lambda workers: {n: None for n in workers}
        self.cs.worker_moneys = lambda workers: {n: "" for n in workers}
        self.cs.usage_word = lambda u: ""
        for w in ({"pending_ask": None}, {"pending_ask": None, "last_summary": None, "task": None}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.cs.print_workers(None, {"w": {**self.worker(), **w}})
            self.assertIn("API 出错", out.getvalue())
        self.assertIn("（无摘要）", out.getvalue())


if __name__ == "__main__":
    unittest.main()
