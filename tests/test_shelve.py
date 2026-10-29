"""shelve refuses a worker whose last report is a PROGRESS with nothing final after it: put away
then, the work it said it was doing is left hanging (tier-switch, 2026-10-28).

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from test_external_sessions import load


class ShelveMidTask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "zh"}))
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "task": "t", "dir": str(self.home), "session": "sid",
                  "window": None, "model": "claude-opus-5-5", "started_at": "2026-10-28T10:00:00+08:00"}}}))
        (self.home / "workers" / "w").mkdir(parents=True)
        self.cs.conversation_exists = lambda w: True
        self.cs.live_holder = lambda sid: None
        self.cs.tmux = lambda *a, **k: ""

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, text):
        self.cs.append_log("w", "worker→secretary", text)

    def shelve(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_shelve(SimpleNamespace(**{"name": "w", "note": "n", "as_name": None, "force": False, **kw}))

    def again(self):
        self.tearDown()
        self.setUp()

    def status(self):
        return self.cs.read_registry()[1]["w"]["status"]

    def test_a_progress_last_is_refused_and_shown(self):
        self.report("TYPE: DONE\nDID: 第一件\nRESULT: r\nDECISIONS: 无")
        self.report("TYPE: PROGRESS\nDID: 在改 tier 开关\nNEXT: 跑回放")
        self.cs.append_log("w", "secretary→worker", "继续")  # what the secretary said doesn't count
        with self.assertRaises(SystemExit) as e:
            self.shelve()
        self.assertIn("last report is a PROGRESS", str(e.exception.code))
        self.assertIn("在改 tier 开关", str(e.exception.code))
        self.assertEqual(self.status(), "PROGRESS")

    def test_force_shelves_it_anyway(self):
        self.report("TYPE: PROGRESS\nDID: x\nNEXT: y")
        self.shelve(force=True)
        self.assertEqual(self.status(), "SHELVED")

    def test_a_final_report_after_the_progress_lets_it_go(self):
        for final in ("DONE", "FAILED", "BLOCKED", "NEED_DECISION"):
            self.again()
            self.report("TYPE: PROGRESS\nDID: x\nNEXT: y")
            self.report(f"TYPE: {final}\nDID: x")
            self.shelve()
            self.assertEqual(self.status(), "SHELVED", final)

    def test_no_report_at_all_is_not_refused(self):
        self.shelve()
        self.assertEqual(self.status(), "SHELVED")

    def test_the_secretarys_exit_and_a_model_switch_still_shelve(self):
        for by in ("exit", "model"):
            self.again()
            self.report("TYPE: PROGRESS\nDID: x\nNEXT: y")
            self.shelve(by=by)
            self.assertEqual(self.status(), "SHELVED", by)

    def test_a_model_switch_says_so(self):
        seen = []
        self.cs.cmd_shelve = lambda args: seen.append(args.by) or sys.exit("stop here")
        with self.assertRaises(SystemExit):
            self.cs.cmd_model(SimpleNamespace(name="w", model="claude-sonnet-5-5", force=False))
        self.assertEqual(seen, ["model"])


if __name__ == "__main__":
    unittest.main()
