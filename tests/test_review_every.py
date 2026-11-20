"""The review comes every review_every_days (3), not every day the user shows up (the user,
2026-11-21: 「复盘三天一次, 不要一天一次.」). The day's first message still carries the sweep.

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class ReviewEvery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        self.cs.stale_items = lambda: []
        self.today = self.cs.dt.date.today()

    def tearDown(self):
        self.tmp.cleanup()

    def reviewed(self, days_ago):
        (self.home / "review-last.date").write_text((self.today - self.cs.dt.timedelta(days=days_ago)).isoformat())

    def first(self):
        (self.home / "user-seen.date").unlink(missing_ok=True)
        return self.cs.first_sight_today()

    def test_no_review_on_record_is_due(self):
        self.assertIn("claudesec review", self.first())

    def test_within_three_days_no_review(self):
        for days in (0, 1, 2):
            with self.subTest(days=days):
                self.reviewed(days)
                self.assertIsNone(self.first())

    def test_three_days_on_it_is_due(self):
        for days in (3, 10):
            with self.subTest(days=days):
                self.reviewed(days)
                self.assertIn("claudesec review", self.first())

    def test_only_the_first_message_of_the_day(self):
        self.reviewed(5)
        self.assertIn("claudesec review", self.first())
        self.assertIsNone(self.cs.first_sight_today())

    def test_old_backups_count_when_there_is_no_date_file(self):
        (self.home / "reviews").mkdir()
        (self.home / "reviews" / f"{(self.today - self.cs.dt.timedelta(days=1)).isoformat()}-memory-before").mkdir()
        (self.home / "reviews" / "2020-01-01-lessons-before.md").write_text("")
        self.assertIsNone(self.first())

    def test_the_sweep_still_comes_daily(self):
        self.reviewed(1)
        self.cs.stale_items = lambda: [object()]
        self.cs.mark_stale = lambda found: None
        self.cs.stale_lines = lambda found: ["- #1 old"]
        context = self.first()
        self.assertIn("[sweep]", context)
        self.assertNotIn("claudesec review", context)

    def test_setting(self):
        self.reviewed(1)
        (self.home / "config.json").write_text(json.dumps({"language": "en", "review_every_days": 1}))
        self.assertIn("claudesec review", self.first())
        (self.home / "config.json").write_text(json.dumps({"language": "en", "review_every_days": 0}))
        (self.home / "review-last.date").unlink()
        self.assertIsNone(self.first())

    def test_constitution_says_three_days(self):
        text = (self.cs.PROMPTS / "secretary.md").read_text()
        self.assertIn("复盘三天一次", text)
        self.assertNotIn("用户上班的日子复盘一次", text)


if __name__ == "__main__":
    unittest.main()
