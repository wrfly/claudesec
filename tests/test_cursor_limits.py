"""Local Cursor workers are kept from eating the machine: at most cursor_max_running at work at
once, each under `nice`.

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class CursorLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.config({})
        self.cs.alive_windows = lambda: {"@1", "@2", "@3"}

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, extra):
        (self.home / "config.json").write_text(json.dumps({"language": "en", **extra}))

    def workers(self, **workers):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": workers}))

    def test_only_cursor_workers_at_work_count(self):
        self.workers(
            a={"agent": "cursor", "status": "PROGRESS", "window": "@1"},
            b={"agent": "cursor", "status": "STARTING", "window": "@2"},
            idle={"agent": "cursor", "status": "DONE", "window": "@3"},          # at its prompt
            gone={"agent": "cursor", "status": "PROGRESS", "window": "@9"},      # window closed
            claude={"agent": "claude", "status": "PROGRESS", "window": "@3"},
        )
        self.assertEqual(self.cs.cursor_busy(), ["a", "b"])
        self.assertEqual(self.cs.cursor_busy(but="a"), ["b"])

    def test_room_refuses_past_the_limit(self):
        self.workers(a={"agent": "cursor", "status": "PROGRESS", "window": "@1"},
                     b={"agent": "cursor", "status": "PROGRESS", "window": "@2"})
        with self.assertRaises(SystemExit) as e:
            self.cs.cursor_room("delegating c", prefix="NOT DELEGATED: ")
        self.assertTrue(str(e.exception.code).startswith("NOT DELEGATED: delegating c: 2 Cursor workers"))
        self.assertIn("a, b", str(e.exception.code))
        self.cs.cursor_room("wake a", but="a")  # carrying on one of them is fine
        self.config({"cursor_max_running": 3})
        self.cs.cursor_room("spawn c")
        self.config({"cursor_max_running": 0})  # 0: no limit
        self.cs.cursor_room("spawn c")

    def test_nice(self):
        self.assertEqual(self.cs.cursor_nice(), "nice -n 10 ")
        self.assertIn("nice -n 10 agent --model m", self.cs.cursor_command("w", "m"))
        self.config({"cursor_nice": 0})
        self.assertEqual(self.cs.cursor_nice(), "")
        self.assertNotIn("nice", self.cs.cursor_command("w", "m"))


if __name__ == "__main__":
    unittest.main()
