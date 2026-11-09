"""A worker's first message (its task, or why it was woken) stays off its agent's command line:
claudesec used to pass `"$(cat initial.md)"`, so the whole task sat in argv. `pkill -f
claude-modelaudit`, meant for modelaudit's leftover Chrome, then also killed the worker whose
task named /tmp/claude-modelaudit/chrome (2026-11-09), and ps showed every task in full. Now
Claude Code gets an @-mention of the file (attached to the message itself), the others a line
telling them to read it.

    python3 -m unittest discover -s tests
"""
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load

TASK = "kill the headless chrome in /tmp/claude-modelaudit/chrome, then report"


class TaskOffArgv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "state"
        self.home.mkdir()
        self.saved = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(Path(self.tmp.name) / "codex")
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        self.d = self.home / "workers" / "w"
        self.d.mkdir(parents=True)
        (self.d / "initial.md").write_text(TASK)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.saved
        self.tmp.cleanup()

    def commands(self):
        cfg = self.cs.config()
        yield "claude", self.cs.AGENTS["claude"].command("w", "m", self.d, cfg)
        yield "claude resume", self.cs.AGENTS["claude"].command("w", "m", self.d, cfg, resume="sid")
        yield "codex", self.cs.AGENTS["codex"].command("w", "default", self.d, cfg)
        for name, rt in self.cs.TTADK_RUNTIMES.items():
            yield f"ttadk {name}", rt.command("w", "m", self.d, cfg)
        yield "cursor", self.cs.cursor_command("w", "m", d=self.d)

    def test_no_task_text_in_any_command(self):
        for label, cmd in self.commands():
            with self.subTest(label):
                self.assertNotIn("claude-modelaudit", cmd)
                self.assertNotIn("$(cat", cmd)
                self.assertIn(str(self.d / "initial.md"), shlex.split(cmd)[-1])

    def test_claude_code_gets_an_at_mention(self):
        cmd = self.cs.AGENTS["claude"].command("w", "m", self.d, self.cs.config())
        self.assertTrue(shlex.split(cmd)[-1].startswith(f"@{self.d / 'initial.md'} "))

    def test_a_path_with_spaces_is_quoted_for_the_mention(self):
        msg = self.cs.mention_message(Path("/x y/initial.md"))
        self.assertTrue(msg.startswith('@"/x y/initial.md" '))

    def test_the_shell_passes_it_as_one_argument(self):
        cmd = self.cs.AGENTS["claude"].command("w", "m", self.d, self.cs.config())
        out = subprocess.run(["sh", "-c", "printf '%s\\n' \"$#\" \"$1\"", "sh", *shlex.split(cmd)[-1:]],
                             capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(out[0], "1")
        self.assertEqual(out[1], self.cs.mention_message(self.d / "initial.md"))

    def test_the_file_still_has_the_task(self):
        self.cs.prepare_worker("w2", self.cs.config(), TASK + "\n", str(self.home))
        self.assertEqual((self.home / "workers" / "w2" / "initial.md").read_text(), TASK + "\n")


if __name__ == "__main__":
    unittest.main()
