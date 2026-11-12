"""plugins/rc, linked into a temporary $CLAUDESEC_HOME the way a user enables it: --rc for the
secretary and the configured workers; the "Loading development channels" box answered only when
server:remote is its only channel and the cursor is on "1. I am using this for local development".
Every other shape of the box, and any other dialog, is left to the user.

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "rc"

BOX = """\
╭──────────────────────────────────────────────────────╮
│ WARNING: Loading development channels                │
│                                                      │
│ Channels: server:remote                              │
│                                                      │
│ ❯ 1. I am using this for local development           │
│   2. Exit                                            │
╰──────────────────────────────────────────────────────╯
"""


class Rc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "plugins").mkdir()
        (self.home / "plugins" / "rc").symlink_to(PLUGIN_DIR)
        self.screen = BOX
        self.keys, self.captures = [], []
        self.cs = load(self.home)
        self.cs.tmux = self.fake_tmux

    def tearDown(self):
        self.tmp.cleanup()

    def fake_tmux(self, *a, check=True):
        if a[0] == "capture-pane":
            self.captures.append(a)
            return self.screen
        self.keys.append(a)
        return ""

    def answer(self, screen=BOX):
        return self.cs.AGENTS["claude"].answer_dialog("w", "@1", screen)

    def test_loaded(self):
        self.assertEqual([ns["__plugin__"] for ns in self.cs.PLUGINS], ["rc"])

    def test_rc_for_the_secretary_and_oncall(self):
        cfg = {}  # the default list
        self.assertEqual(self.cs.plugin_args(cfg, {"role": "secretary"}), ["--rc"])
        self.assertEqual(self.cs.plugin_args(cfg, {"role": "worker", "name": "oncall"}), ["--rc"])
        self.assertEqual(self.cs.plugin_args(cfg, {"role": "worker", "name": "other"}), [])

    def test_rc_workers_from_config(self):
        (self.home / "config.json").write_text(json.dumps({"rc_workers": "mine"}))
        cfg = self.cs.config()
        self.assertEqual(self.cs.plugin_args(cfg, {"role": "worker", "name": "mine"}), ["--rc"])
        self.assertEqual(self.cs.plugin_args(cfg, {"role": "worker", "name": "oncall"}), [])

    def test_our_box_is_answered(self):
        self.assertTrue(self.answer())
        self.assertEqual(self.keys, [("send-keys", "-t", "@1", "Enter")])

    def test_it_reads_the_pane_when_given_no_screen(self):
        self.assertTrue(self.answer(None))
        self.assertEqual(self.keys, [("send-keys", "-t", "@1", "Enter")])

    def test_a_screen_without_the_box_is_not_read(self):
        self.assertFalse(self.answer("Do you trust the files in this folder?"))
        self.assertEqual((self.captures, self.keys), ([], []))

    def test_another_channel_is_left_to_the_user(self):
        self.screen = BOX.replace("Channels: server:remote", "Channels: server:other")
        self.assertFalse(self.answer(self.screen))
        self.assertEqual(self.keys, [])

    def test_more_than_one_channel_is_left_to_the_user(self):
        self.screen = BOX.replace("Channels: server:remote", "Channels: server:remote, server:other")
        self.assertFalse(self.answer(self.screen))
        self.assertEqual(self.keys, [])

    def test_the_cursor_elsewhere_is_left_to_the_user(self):
        self.screen = BOX.replace("❯ 1.", "  1.").replace("  2. Exit", "❯ 2. Exit")
        self.assertFalse(self.answer(self.screen))
        self.assertEqual(self.keys, [])

    def test_a_box_with_no_channel_line_is_left_to_the_user(self):
        self.screen = BOX.replace("Channels: server:remote", "")
        self.assertFalse(self.answer(self.screen))
        self.assertEqual(self.keys, [])

    def test_the_box_must_still_be_up_when_it_looks_again(self):
        self.screen = "the worker went on"
        self.assertFalse(self.answer())  # the screen said the box was there, the pane disagrees
        self.assertEqual(self.keys, [])


if __name__ == "__main__":
    unittest.main()
