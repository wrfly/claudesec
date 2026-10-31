"""Plugins in $CLAUDESEC_HOME/plugins/<name>/plugin.py: none, and nothing is different; one adds
arguments and prompt text to the Claude Code sessions claudesec starts (knowing which session),
answers a dialog of its own, and hears of reports; a broken one is skipped.

    python3 -m unittest discover -s tests
"""
import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_external_sessions import load

PLUGIN = '''
seen = []

def claude_args(cfg, who):
    if who["role"] == "secretary":
        return ["--sec-flag"]
    return ["--talk"] if who["discuss"] else ["--work", who["agent"]]

def prompt(cfg, who):
    return "EXTRA FOR " + who["role"] + (" " + who["name"] if who["role"] == "worker" else "")

def answer_dialog(target, screen):
    if "PLUGIN DIALOG" in (screen if screen is not None else cs.pane_text(target)):
        cs.tmux("send-keys", "-t", target, "Enter")
        return True
    return False

def reported(cfg, who, typ):
    seen.append((who, typ))
'''


class Plugins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.keys = []

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, plugins=None):
        for name, text in (plugins or {}).items():
            (self.home / "plugins" / name).mkdir(parents=True, exist_ok=True)
            (self.home / "plugins" / name / "plugin.py").write_text(text)
        cs = load(self.home)
        cs.fresh_prompt = lambda: []
        cs.tmux = lambda *a, check=True: self.keys.append(a[-1]) or ""
        return cs

    def secretary_argv(self, cs):
        started = []

        class Child:
            def __init__(self, argv, **kw):
                started.append(argv)

            def wait(self):
                return 0

        with mock.patch.object(cs.subprocess, "Popen", Child), \
                mock.patch.object(cs, "detach", lambda job: None), \
                mock.patch.object(cs, "memory_settings", lambda d: {}), \
                mock.patch.object(cs.signal, "signal", lambda *a: None), \
                mock.patch.object(cs.os, "chdir", lambda d: None), \
                mock.patch.dict(os.environ, {"TMUX_PANE": ""}):
            cs.cmd_run(argparse.Namespace(new=False))
        return started[0]

    def worker_dir(self, cs, name, mode):
        return cs.prepare_worker(name, cs.config(), "hi", str(self.home), mode=mode)

    def test_no_plugins_nothing_added(self):
        cs = self.load()
        self.assertEqual(cs.PLUGINS, [])
        d = self.worker_dir(cs, "w", "task")
        cmd = cs.AGENTS["claude"].command("w", "claude-opus-5-5", d, cs.config(), resume="sid", prompt=False)
        self.assertTrue(cmd.endswith("--append-system-prompt-file " + str(d / "prompt.md") + " --resume sid"), cmd)
        self.assertNotIn("EXTRA", (d / "prompt.md").read_text())
        self.assertNotIn("EXTRA", cs.secretary_prompt().read_text())
        self.assertFalse(cs.answer_plugin_dialog("@1", "PLUGIN DIALOG"))

    def test_arguments_know_the_session(self):
        cs = self.load({"p": PLUGIN})
        self.assertIn("--sec-flag", self.secretary_argv(cs))
        task, talk = self.worker_dir(cs, "w", "task"), self.worker_dir(cs, "t", "discuss")
        cmd = cs.AGENTS["claude"].command("w", "claude-opus-5-5", task, cs.config(), resume="sid", prompt=False)
        self.assertIn("--work claude --resume sid", cmd)
        self.assertIn("--talk", cs.AGENTS["claude"].command("t", "claude-opus-5-5", talk, cs.config()))

    def test_prompt_text_goes_at_the_end(self):
        cs = self.load({"p": PLUGIN})
        self.assertTrue(cs.secretary_prompt().read_text().endswith("\nEXTRA FOR secretary\n"))
        d = self.worker_dir(cs, "w", "task")
        self.assertTrue((d / "prompt.md").read_text().endswith("\nEXTRA FOR worker w\n"))

    def test_its_dialog_is_answered(self):
        cs = self.load({"p": PLUGIN})
        self.assertTrue(cs.AGENTS["claude"].answer_dialog("w", "@1", "PLUGIN DIALOG"))
        self.assertEqual(self.keys, ["Enter"])
        self.assertFalse(cs.answer_plugin_dialog("@1", "something else"))
        cs.pane_text = lambda target, suggestion="mark": "PLUGIN DIALOG"
        with mock.patch.object(cs.time, "sleep", lambda s: None):
            cs.answer_own_dialogs("%1", secs=3)
        self.assertEqual(self.keys, ["Enter", "Enter"])

    def test_it_hears_of_reports(self):
        cs = self.load({"p": PLUGIN})
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "t": {"status": "RUNNING", "mode": "discuss", "agent": "claude", "window": "@2"}}}))
        cs.record_report("t", "TYPE: DONE\nDID: talked")
        self.assertEqual(cs.PLUGINS[0]["seen"],
                         [({"role": "worker", "name": "t", "agent": "claude", "discuss": True}, "DONE")])

    def test_a_broken_plugin_is_skipped(self):
        with mock.patch("sys.stderr"):
            cs = self.load({"a-broken": "raise RuntimeError('no')\n", "b-good": PLUGIN,
                            "c-raises": "def prompt(cfg, who):\n    raise ValueError('x')\n"})
            self.assertEqual(len(cs.PLUGINS), 2)
            self.assertTrue(cs.secretary_prompt().read_text().endswith("\nEXTRA FOR secretary\n"))


if __name__ == "__main__":
    unittest.main()
