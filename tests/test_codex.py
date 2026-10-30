"""Codex workers: OpenAI's codex CLI run through `ttadk codex`, reporting through hooks in
~/.codex/hooks.json; and Claude Code workers on a non-Claude model, run through `ttadk claude`.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class Codex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "state"
        self.home.mkdir()
        self.saved_env = {k: os.environ.get(k) for k in ("CODEX_HOME", "CLAUDESEC_WORKER")}
        os.environ["CODEX_HOME"] = str(self.root / "codex")  # never the real ~/.codex
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        self.cs.secretary_alive = lambda: True
        self.alerts = []
        self.cs.raise_alert = self.alerts.append

    def tearDown(self):
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def workers(self, **workers):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": workers}))

    def hook(self, fn, payload, worker):
        os.environ["CLAUDESEC_WORKER"] = worker
        out, saved = io.StringIO(), sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            with contextlib.redirect_stdout(out):
                fn()
        finally:
            sys.stdin = saved
        return out.getvalue()

    def test_hooks_keep_the_users_and_replace_claudesecs(self):
        path = self.root / "codex" / "hooks.json"
        path.parent.mkdir()
        mine = {"hooks": [{"type": "command", "command": "echo mine"}]}
        stale = {"hooks": [{"type": "command", "command": '[ -z "${CLAUDESEC_WORKER:-}" ] || old hook codex-stop'}]}
        path.write_text(json.dumps({"hooks": {"Stop": [mine, stale], "PreToolUse": [stale]}}))
        self.cs.install_codex_hooks()
        hooks = json.loads(path.read_text())["hooks"]
        self.assertEqual(sorted(hooks), ["SessionStart", "Stop", "UserPromptSubmit"])
        self.assertEqual(hooks["Stop"][0], mine)
        self.assertEqual(len(hooks["Stop"]), 2)
        self.assertEqual(hooks["SessionStart"][0]["hooks"][0]["additionalContextLimit"], 0)
        self.assertEqual(len(list(path.parent.glob("hooks.json.bak-claudesec-*"))), 1)
        before = path.read_text()
        self.cs.install_codex_hooks()  # unchanged: no rewrite, no second backup
        self.assertEqual(path.read_text(), before)
        self.assertEqual(len(list(path.parent.glob("hooks.json.bak-claudesec-*"))), 1)
        # outside a worker every entry is a no-op that exits 0
        env = {k: v for k, v in os.environ.items() if k != "CLAUDESEC_WORKER"}
        for event in ("SessionStart", "Stop", "UserPromptSubmit"):
            cmd = hooks[event][-1]["hooks"][0]["command"].replace(str(self.cs.SELF), "/bin/false")
            r = subprocess.run(["sh", "-c", cmd], env=env, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), event)

    def test_codex_command(self):
        cmd = self.cs.codex_command("w", "gpt-6.1-sol", "resume", "sid-1")
        self.assertIn("CLAUDESEC_WORKER=w ", cmd)
        self.assertIn("ttadk codex --mode ttadk-only -m gpt-6.1-sol --skip-check --", cmd)
        self.assertIn("check_for_update_on_startup=false", cmd)
        self.assertTrue(cmd.endswith("--dangerously-bypass-approvals-and-sandbox resume sid-1"))
        d = self.root / "d"
        self.assertTrue(self.cs.codex_command("w", "m", d=d).endswith(f'"$(cat {d / "initial.md"})"'))

    def test_a_non_claude_model_runs_through_ttadk_claude(self):
        self.assertFalse(self.cs.ttadk_model("claude-opus-5-5"))
        self.assertFalse(self.cs.ttadk_model("opus"))
        self.assertTrue(self.cs.ttadk_model("deepseek-v4-pro-0813"))
        cfg = {**self.cs.config(), "remote_control": True}
        d = self.root / "d"
        cmd = self.cs.worker_command("w", d, cfg, "deepseek-v4-pro-0813")
        self.assertTrue(cmd.startswith("ttadk claude -m deepseek-v4-pro-0813 --skip-check -- -n w "), cmd)
        self.assertNotIn("--rc", cmd)  # remote control needs a claude.ai login
        self.assertTrue(self.cs.worker_command("w", d, cfg, "claude-opus-5-5").startswith("claude --model claude-opus-5-5 "))

    def test_delegate_section_shows_with_codex_alone(self):
        self.cs.cursor_available = lambda: (False, "")
        text = "a\n<!-- if delegate -->\nD\n<!-- else -->\nE\n<!-- end delegate -->\n<!-- if cursor -->\nC\n<!-- end cursor -->\nz\n"
        self.cs.codex_available = lambda: (True, "")
        self.assertEqual(self.cs.render_prompt(text), "a\nD\nz\n")
        self.cs.codex_available = lambda: (False, "")
        self.assertEqual(self.cs.render_prompt(text), "a\nE\nz\n")
        for prompt in ("worker.md", "secretary.md", "worker-cursor.md"):  # every marker pairs up
            self.assertNotIn("<!-- ", self.cs.render_prompt((self.cs.PROMPTS / prompt).read_text()), prompt)

    def test_session_start_records_the_id_and_hands_over_the_rules(self):
        self.workers(c={"status": "STARTING", "agent": "codex", "window": "@1", "dir": "/tmp", "task": "t"})
        self.cs.worker_dir("c").mkdir(parents=True, exist_ok=True)
        (self.cs.worker_dir("c") / "prompt.md").write_text("RULES")
        out = json.loads(self.hook(self.cs.hook_codex_session_start, {"session_id": "s-1"}, "c"))
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "RULES")
        self.assertEqual(self.cs.read_registry()[1]["c"]["session"], "s-1")
        self.assertEqual(self.hook(self.cs.hook_codex_session_start, {}, "nobody"), "")

    def test_stop_asks_for_a_report_once(self):
        self.workers(c={"status": "PROGRESS", "agent": "codex", "window": "@1", "dir": "/tmp", "task": "t"})
        d = self.cs.worker_dir("c")
        d.mkdir(parents=True, exist_ok=True)
        (d / "busy").write_text("x")
        out = json.loads(self.hook(self.cs.hook_codex_stop, {"stop_hook_active": False}, "c"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("report c", out["reason"])
        self.assertTrue((d / "busy").exists())
        # asked once already: let it stop, mark it, tell the secretary
        self.assertEqual(json.loads(self.hook(self.cs.hook_codex_stop, {"stop_hook_active": True}, "c")), {})
        self.assertTrue((d / "ended-unreported").exists())
        self.assertFalse((d / "busy").exists())
        self.assertEqual(len(self.alerts), 1)

    def test_stop_after_a_report_lets_it_end(self):
        self.workers(c={"status": "DONE", "agent": "codex", "window": "@1", "dir": "/tmp", "task": "t"})
        d = self.cs.worker_dir("c")
        d.mkdir(parents=True, exist_ok=True)
        (d / "reported").write_text("x")
        (d / "busy").write_text("x")
        self.assertEqual(json.loads(self.hook(self.cs.hook_codex_stop, {}, "c")), {})
        self.assertFalse((d / "reported").exists())
        self.assertFalse((d / "busy").exists())
        self.assertFalse((d / "ended-unreported").exists())
        self.assertEqual(json.loads(self.hook(self.cs.hook_codex_stop, {}, "nobody")), {})

    def test_dialogs_answered_and_not(self):
        sent = []
        self.cs.tmux = lambda *a, **k: sent.append(a)
        self.cs.time.sleep = lambda s: None
        trust = "Do you trust the contents of this directory?\n› 1. Yes, continue\n  2. No, quit"
        resume = "Choose working directory to resume this session\n› 1. Use session directory\n  2. Use current directory"
        hooks = "Hooks need review\n› 1. Review hooks\n  2. Trust all and continue\n  3. Continue without trusting"
        self.assertTrue(self.cs.answer_codex_dialog("@1", trust))
        self.assertTrue(self.cs.answer_codex_dialog("@1", resume))
        self.assertFalse(self.cs.answer_codex_dialog("@1", hooks))  # the user's to trust
        self.assertEqual([a[-1] for a in sent], ["1", "2"])

    def delegate(self, *extra):
        self.workers(boss={"status": "PROGRESS", "dir": str(self.root), "task": "t"})
        got = []
        self.cs.cmd_spawn = lambda args: got.append(args.agent)
        self.cs.cursor_available = self.cs.codex_available = lambda: (True, "test")
        out, saved_argv, saved_stdin = io.StringIO(), sys.argv, sys.stdin
        sys.argv = ["claudesec", "delegate", "boss", "--name", "j", "--dir", str(self.root), *extra]
        sys.stdin = io.StringIO("count the files")
        try:
            with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        return got

    def test_delegate_goes_to_codex_unless_told(self):
        self.assertEqual(self.delegate(), ["codex"])
        self.assertEqual(self.delegate("--agent", "cursor"), ["cursor"])


if __name__ == "__main__":
    unittest.main()
