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
