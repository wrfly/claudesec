"""Workers done over an hour ago with nothing of theirs waiting in the queue are marked「[收尾]」
in `claudesec status` and the progress pane, for the secretary to close or shelve.

    python3 -m unittest discover -s tests
"""
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class WrapUp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))
        self.cs.lang.cache_clear()
        now = dt.datetime.now().astimezone()
        ago = lambda h: (now - dt.timedelta(hours=h)).isoformat()
        self.workers = {
            "old": {"status": "DONE", "last_report_at": ago(2), "model": "claude-opus-5-5", "window": "@1", "task": "t"},
            "fresh": {"status": "DONE", "last_report_at": ago(0.2), "model": "claude-opus-5-5", "window": "@2", "task": "t"},
            "asked": {"status": "DONE", "last_report_at": ago(3), "model": "claude-opus-5-5", "window": "@3", "task": "t"},
            "busy": {"status": "PROGRESS", "last_report_at": ago(5), "model": "claude-opus-5-5", "window": "@4", "task": "t"},
            "deleg": {"status": "DONE", "last_report_at": ago(5), "model": "claude-opus-5-5", "window": "@5", "task": "t",
                      "delegated_by": "old"},
        }
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": self.workers}))
        (self.home / "queue.md").write_text(self.cs.queue_template())
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_queue(self.cs.argparse.Namespace(action="add", words=["[asked] which one?"], asked=True, later=False))

    def tearDown(self):
        self.cs.lang.cache_clear()
        self.tmp.cleanup()

    def test_which(self):
        self.assertEqual(self.cs.to_wrap_up(self.workers), {"old"})

    def test_not_with_something_running_in_the_background(self):
        d = self.cs.worker_dir("old")
        d.mkdir(parents=True, exist_ok=True)
        (d / "last-stop.json").write_text(json.dumps({"background_tasks": [
            {"id": "b1", "type": "shell", "status": "running", "command": "poll.sh"}], "session_crons": []}))
        self.assertEqual(self.cs.to_wrap_up(self.workers), set())
        (d / "last-stop.json").write_text(json.dumps({"background_tasks": [{"status": "completed"}]}))
        self.assertEqual(self.cs.to_wrap_up(self.workers), {"old"})

    def test_shown_in_status(self):
        self.cs.alive_windows = lambda: {"@1", "@2", "@3", "@4", "@5"}
        self.cs.worker_usage = lambda ws: {n: None for n in ws}
        self.cs.worker_moneys = lambda ws: {n: "" for n in ws}
        self.cs.usage_word = lambda u: ""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cs.print_workers(None, self.workers)
        lines = {l.split()[0]: l for l in out.getvalue().splitlines()[1:] if l.strip()}
        self.assertIn("[收尾]", lines["old"])
        self.assertNotIn("窗口已关", lines["old"])
        for name in ("fresh", "asked", "busy", "deleg"):
            self.assertNotIn("[收尾]", lines[name], name)


if __name__ == "__main__":
    unittest.main()
