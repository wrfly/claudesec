"""What a model's tokens cost: longest-prefix match, fast mode, config overrides, unpriced models.

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class Prices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_longest_prefix_wins(self):
        self.assertEqual(self.cs.price_of("claude-opus-5-5"), [4, 5, 8, 0.20, 20])
        self.assertEqual(self.cs.price_of("claude-opus-5"), [5, 6.25, 10, 0.50, 25])
        self.assertEqual(self.cs.price_of("claude-haiku-4-5-20251001"), [1, 1.25, 2, 0.10, 5])
        self.assertEqual(self.cs.price_of("claude-sonnet-5-5")[3], 0.10)  # not 0.20

    def test_fast_mode_and_unpriced(self):
        self.assertEqual(self.cs.price_of("claude-opus-5-5", "fast"), [8, 10, 16, 0.40, 40])
        self.assertIsNone(self.cs.price_of("claude-sonnet-5-5", "fast"))  # no fast mode there
        for model in ("deepseek-v4-pro", "<synthetic>", "claude-opus", "claude-opus-55"):
            self.assertIsNone(self.cs.price_of(model), model)
        self.assertIsNone(self.cs.cost_of([1, 1, 1, 1, 1], "deepseek-v4-pro"))

    def test_cost_counts_each_kind_at_its_price(self):
        # 1M of each: 4 + 5 + 8 + 0.2 + 20
        self.assertAlmostEqual(self.cs.cost_of([10**6] * 5, "claude-opus-5-5"), 37.2)
        self.assertAlmostEqual(self.cs.cost_of([0, 0, 2 * 10**6, 0, 0], "claude-opus-5-5"), 16.0)

    def test_config_adds_and_overrides(self):
        (self.home / "config.json").write_text(json.dumps({"prices": {
            "deepseek-v4": [0.5, 0.5, 0.5, 0.05, 2], "claude-opus-5-5": [1, 1, 1, 1, 1]}}))
        self.assertEqual(self.cs.price_of("deepseek-v4-pro"), [0.5, 0.5, 0.5, 0.05, 2])
        self.assertEqual(self.cs.price_of("claude-opus-5-5"), [1, 1, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()


class Cost(unittest.TestCase):
    """`claudesec cost` and the $ column."""

    def setUp(self):
        import os
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "state"
        self.home.mkdir()
        self.cs = load(self.home)
        self.saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root / "config")
        self.project = self.root / "config" / "projects" / "-p"
        self.project.mkdir(parents=True)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))

    def tearDown(self):
        import os
        if self.saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved
        self.tmp.cleanup()

    def line(self, mid, model, w1h=0, out=0, ts="2026-10-26T01:00:00Z"):
        return json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "usage": {
            "input_tokens": 0, "cache_creation_input_tokens": w1h, "cache_read_input_tokens": 0,
            "output_tokens": out, "cache_creation": {"ephemeral_1h_input_tokens": w1h}}}}) + "\n"

    def test_cost_lists_priced_and_unpriced_by_worker(self):
        (self.project / "s1.jsonl").write_text(self.line("a", "claude-opus-5-5", w1h=10**6, out=10**6)
                                               + self.line("b", "deepseek-v4-pro", out=500))
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "PROGRESS", "session": "s1", "dir": "/tmp", "task": "t", "budget_usd": 50}}}))
        import contextlib
        import io
        import sys
        out, saved = io.StringIO(), sys.argv
        sys.argv = ["claudesec", "cost"]
        try:
            with contextlib.redirect_stdout(out):
                self.cs.main()
        finally:
            sys.argv = saved
        text = out.getvalue()
        self.assertIn("$28.0", text)  # 1M 1h writes at $8 + 1M output at $20
        self.assertIn("unpriced", text)
        self.assertIn("deepseek-v4-pro", text.splitlines()[-1])
        spent = self.cs.worker_cost({"w": {"session": "s1"}})["w"]
        self.assertAlmostEqual(spent, 28.0)
        self.assertEqual(self.cs.money_word(spent, 50), "$28.0 / $50.0")
        self.assertEqual(self.cs.money_word(None), "-")

    def test_moments_for_a_cost_period_lie_in_the_past(self):
        import datetime as dt
        at = dt.datetime(2026, 10, 26, 15, 0).astimezone()
        self.assertEqual(self.cs.parse_moment("09:00", at).hour, 9)
        self.assertEqual(self.cs.parse_moment("09:00", at).day, 26)
        self.assertEqual(self.cs.parse_moment("16:00", at).day, 25)  # later today hasn't come: yesterday's
        self.assertEqual(self.cs.parse_moment("3h", at), at - dt.timedelta(hours=3))
        self.assertEqual(self.cs.parse_moment("2026-10-25", at).date(), dt.date(2026, 10, 25))


class MoneyColumn(Cost):
    def test_today_by_default_and_budget_against_all(self):
        (self.project / "s1.jsonl").write_text(
            self.line("old", "claude-opus-5-5", out=10**6, ts="2026-01-01T01:00:00Z")
            + self.line("new", "claude-opus-5-5", out=10**5, ts=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat()))
        m = self.cs.worker_moneys({"a": {"session": "s1"}, "b": {"session": "s1", "budget_usd": 50}})
        self.assertEqual(m["a"], "$2.0")  # today only: 0.1M output at $20
        self.assertEqual(m["b"], "$22.0 / $50.0")  # against the budget: all of it
