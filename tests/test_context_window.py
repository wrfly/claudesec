"""A worker on a model ending in [1m] gets a 1M context in its settings.json: through a gateway
Claude Code drops the [1m] and sizes the context by modelSettings.<id>.autoCompactWindow, which
the user's settings may hold at 200k. Without [1m] the worker's settings say nothing about it.

    python3 -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_external_sessions import load


class ContextWindow(unittest.TestCase):
    def setUp(self):
        # ANTHROPIC_DEFAULT_<ALIAS>_MODEL overrides the alias table: the machine running the tests
        # may set them, so each test starts without and sets the ones it means to.
        env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_DEFAULT_")},
                              clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        self.d = self.cs.prepare_worker("w", {**self.cs.config(), "secretary_name": "secretary"}, "hi", self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self, model):
        cmd = self.cs.AGENTS["claude"].command("w", model, self.d, self.cs.config())
        return cmd, json.loads((self.d / "settings.json").read_text())

    def test_1m_models_get_the_window_for_their_id_and_keep_the_hooks(self):
        cmd, conf = self.settings("claude-opus-5-5[1m]")
        self.assertIn("'claude-opus-5-5[1m]'", cmd)
        self.assertEqual(conf["modelSettings"], {"claude-opus-5-5": {"autoCompactWindow": 1_000_000}})
        self.assertIn("SessionStart", conf["hooks"])

    def test_an_alias_is_resolved_by_env_or_the_table(self):
        _, conf = self.settings("opus[1m]")
        self.assertEqual(conf["modelSettings"]["claude-opus-5-5"], {"autoCompactWindow": 1_000_000})
        with mock.patch.dict(os.environ, {"ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-9"}):
            _, conf = self.settings("sonnet[1m]")
        self.assertIn("claude-sonnet-9", conf["modelSettings"])
        with mock.patch.dict(os.environ, {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5[1m]"}):
            _, conf = self.settings("opus[1m]")
        self.assertEqual(conf["modelSettings"]["claude-opus-5-5"], {"autoCompactWindow": 1_000_000})
        self.assertNotIn("claude-opus-5-5[1m]", conf["modelSettings"])

    def test_without_1m_nothing_is_set(self):
        _, conf = self.settings("claude-opus-5-5")
        self.assertNotIn("modelSettings", conf)


if __name__ == "__main__":
    unittest.main()
