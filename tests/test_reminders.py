"""Reminders list in the order they come due, whatever offset each due time was written with.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


def item(rid, due, status="pending"):
    return {"id": rid, "due": due, "status": status, "target": "w", "text": rid, "every": None}


class ReminderOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        # As stored on 2026-10-22: Z times kept +00:00, local ones +08:00.
        items = [item("r6", "2026-10-26T09:00:00+08:00"), item("r7", "2026-10-23T02:50:00+08:00"),
                 item("r8", "2026-10-22T21:00:00+00:00"), item("r9", "2026-10-22T19:30:00+00:00"),
                 item("r11", "2026-10-23T01:00:00+00:00"), item("r10", "2026-10-22T19:30:00+00:00", "cancelled"),
                 item("r1", "2026-10-21T09:05:00+00:00", "fired")]
        (self.home / "reminders.json").write_text(json.dumps({"next": 12, "items": items}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_by_due_is_time_order_then_number(self):
        got = [r["id"] for r in sorted(self.cs.read_reminders(), key=self.cs.by_due)]
        # 10-22 18:50Z r7, 19:30Z r9 / r10 (same moment: by number), 21:00Z r8, 10-23 01:00Z r11, r6
        self.assertEqual(got, ["r1", "r7", "r9", "r10", "r8", "r11", "r6"])

    def listed(self, *flags):
        """The ids `claudesec remind --list [flags]` prints, top to bottom."""
        out, saved = io.StringIO(), sys.argv
        sys.argv = ["claudesec", "remind", "--list", *flags]
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv = saved
        return [line.split()[0] for line in out.getvalue().splitlines()[1:] if line.strip()]

    def test_list_and_list_all_are_in_due_order(self):
        self.assertEqual(self.listed(), ["r7", "r9", "r8", "r11", "r6"])
        self.assertEqual(self.listed("--all"), ["r1", "r7", "r9", "r10", "r8", "r11", "r6"])


if __name__ == "__main__":
    unittest.main()
