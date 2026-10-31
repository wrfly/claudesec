"""The agents a worker runs on (bin/claudesec's Agent, agents/*.py): Codex, plain or through
`ttadk codex`, reporting through hooks in ~/.codex/hooks.json; Cursor through ~/.cursor/hooks.json;
ttadk picking Codex or Claude Code by model; old registry records mapped onto them.

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


class Agents(unittest.TestCase):
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

    def on(self, **avail):
        """Agents available here or not, whatever is installed."""
        for name, ok in avail.items():
            objs = [self.cs.AGENTS[name], *(self.cs.TTADK_RUNTIMES.values() if name == "ttadk" else [])]
            for obj in objs:
                obj.available = lambda ok=ok: (ok, "test")

    def test_hooks_keep_the_users_and_replace_claudesecs(self):
        codex = self.cs.AGENTS["codex"]
        path = codex.hooks_file()
        path.parent.mkdir(parents=True)
        mine = {"hooks": [{"type": "command", "command": "echo mine"}]}
        stale = {"hooks": [{"type": "command", "command": '[ -z "${CLAUDESEC_WORKER:-}" ] || old hook codex-stop'}]}
        path.write_text(json.dumps({"hooks": {"Stop": [mine, stale], "PreToolUse": [stale]}}))
        codex.install()
        hooks = json.loads(path.read_text())["hooks"]
        self.assertEqual(sorted(hooks), ["SessionStart", "Stop", "UserPromptSubmit"])
        self.assertEqual(hooks["Stop"][0], mine)
        self.assertEqual(len(hooks["Stop"]), 2)
        self.assertTrue(hooks["Stop"][1]["hooks"][0]["command"].endswith("hook agent-stop codex"))
        self.assertEqual(hooks["SessionStart"][0]["hooks"][0]["additionalContextLimit"], 0)
        self.assertEqual(len(list(path.parent.glob(path.name + ".bak-claudesec-*"))), 1)
        before = path.read_text()
        self.cs.TTADK_RUNTIMES["codex"].install()  # the same hooks: no rewrite, no second backup
        self.assertEqual(path.read_text(), before)
        self.assertEqual(len(list(path.parent.glob(path.name + ".bak-claudesec-*"))), 1)
        # outside a worker every entry is a no-op that exits 0
        env = {k: v for k, v in os.environ.items() if k != "CLAUDESEC_WORKER"}
        for event in ("SessionStart", "Stop", "UserPromptSubmit"):
            cmd = hooks[event][-1]["hooks"][0]["command"].replace(str(self.cs.SELF), "/bin/false")
            r = subprocess.run(["sh", "-c", cmd], env=env, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), event)

    def test_cursor_hooks_move_to_agent_names(self):
        cursor = self.cs.AGENTS["cursor"]
        path = cursor.hooks_file()
        path.parent.mkdir(parents=True)
        old = {"command": '[ -z "${CLAUDESEC_WORKER:-}" ] || /gone/bin/claudesec hook cursor-stop'}
        path.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "mine"}, old]}}))
        cursor.install()
        hooks = json.loads(path.read_text())["hooks"]
        self.assertEqual(hooks["stop"][0], {"command": "mine"})
        self.assertEqual([h["command"].split("claudesec ")[-1] for h in hooks["stop"][1:]], ["hook agent-stop cursor"])
        self.assertTrue(hooks["sessionStart"][0]["command"].endswith("hook agent-start cursor"))
        for old_name in ("cursor-session-start", "cursor-prompt", "cursor-stop", "cursor-launch", "cursor-exit"):
            self.assertIn(old_name, self.cs.HOOKS)  # what windows started before still call

    def test_codex_command(self):
        cfg = self.cs.config()
        d = self.root / "d"
        cmd = self.cs.AGENTS["codex"].command("w", "default", d, cfg, resume="sid-1", prompt=False)
        self.assertTrue(cmd.startswith("CLAUDESEC_WORKER=w "))
        self.assertIn(" codex -c check_for_update_on_startup=false", cmd)
        self.assertTrue(cmd.endswith("--dangerously-bypass-approvals-and-sandbox resume sid-1"))
        self.assertIn("codex -m gpt-x -c", self.cs.AGENTS["codex"].command("w", "gpt-x", d, cfg))
        cmd = self.cs.TTADK_RUNTIMES["codex"].command("w", "gpt-6.1-sol", d, cfg)
        self.assertIn("ttadk codex --mode ttadk-only -m gpt-6.1-sol --skip-check -- -c", cmd)
        self.assertTrue(cmd.endswith(f'"$(cat {d / "initial.md"})"'))

    def test_ttadk_picks_the_runtime_by_model(self):
        ttadk = self.cs.AGENTS["ttadk"]
        self.assertEqual(ttadk.runtime("gpt-6.1-sol").runtime_name, "codex")
        self.assertEqual(ttadk.runtime("o4-mini").runtime_name, "codex")
        self.assertEqual(ttadk.runtime("deepseek-v4-pro-0813").runtime_name, "claude")
        self.assertEqual(ttadk.runtime("gpt-6.1-sol", "claude").runtime_name, "claude")  # the one it started on
        self.assertTrue(ttadk.runtime("gpt-6.1-sol").external)
        self.assertFalse(ttadk.runtime("deepseek-v4-pro-0813").external)
        cfg = self.cs.config()
        d = self.root / "d"
        cmd = ttadk.runtime("deepseek-v4-pro-0813").command("w", "deepseek-v4-pro-0813", d, cfg)
        self.assertTrue(cmd.startswith("ttadk claude -m deepseek-v4-pro-0813 --skip-check -- -n w "), cmd)
        self.assertTrue(self.cs.AGENTS["claude"].command("w", "claude-opus-5-5", d, cfg)
                        .startswith("claude --model claude-opus-5-5 "))

    def test_old_records_map_onto_the_agents(self):
        of = self.cs.agent_of
        self.assertEqual(of({}).name, "claude")
        self.assertEqual(of({"model": "claude-opus-5-5"}).name, "claude")
        old_ttadk_claude = of({"agent": "claude", "model": "deepseek-v4-pro-0813"})  # spawned before ttadk was an agent
        self.assertEqual((old_ttadk_claude.name, old_ttadk_claude.runtime_name), ("ttadk", "claude"))
        old_codex = of({"agent": "codex", "model": "gpt-6.1-sol"})  # Codex ran through ttadk then
        self.assertEqual((old_codex.name, old_codex.runtime_name), ("ttadk", "codex"))
        self.assertEqual(of({"agent": "codex", "runtime": None, "model": None}).runtime_name, "codex")
        self.assertIs(of({"agent": "cursor"}), self.cs.AGENTS["cursor"])
        self.assertEqual(self.cs.agent_fields({"agent": "codex", "model": "gpt-6.1-sol"}),
                         {"agent": "ttadk", "runtime": "codex"})
        self.assertEqual(self.cs.agent_fields({"model": "claude-opus-5-5"}), {})
        self.assertTrue(self.cs.external_record({"tags": ["delegated", "cursor"]}))
        self.assertFalse(self.cs.external_record({"agent": "ttadk", "runtime": "claude"}))

    def test_spawn_refuses_claude_on_another_model(self):
        with self.assertRaises(SystemExit) as e:
            self.cs.cmd_spawn(self.cs.argparse.Namespace(
                name="w", dir=str(self.root), model="deepseek-v4-pro-0813", discuss=False, agent="claude",
                for_worker=None, budget=None, task="t"))
        self.assertIn("--agent ttadk", str(e.exception.code))

    def test_prompt_parts_by_agent(self):
        self.on(cursor=False, codex=False, ttadk=True)
        text = ("a\n<!-- if delegate -->\nD\n<!-- else -->\nE\n<!-- end delegate -->\n"
                "<!-- if cursor -->\nC\n<!-- end cursor -->\n<!-- if ttadk -->\nT\n<!-- end ttadk -->\nz\n")
        self.assertEqual(self.cs.render_prompt(text), "a\nD\nT\nz\n")
        self.on(ttadk=False, codex=True)
        self.assertEqual(self.cs.render_prompt(text), "a\nD\nz\n")
        self.on(codex=False)
        self.assertEqual(self.cs.render_prompt(text), "a\nE\nz\n")
        nested = "<!-- if delegate -->\nD\n<!-- if cursor -->\nC\n<!-- end cursor -->\n<!-- else -->\nE\n<!-- end delegate -->\n"
        self.on(cursor=True)
        self.assertEqual(self.cs.render_prompt(nested), "D\nC\n")
        for prompt in ("worker.md", "secretary.md", "worker-external.md"):  # every marker pairs up
            self.assertNotIn("<!-- ", self.cs.render_prompt((self.cs.PROMPTS / prompt).read_text()), prompt)

    def test_delegate_agents_in_config_order(self):
        self.assertEqual(self.cs.delegate_agents(), ["ttadk", "codex", "cursor"])
        (self.home / "config.json").write_text(json.dumps({"delegate_agent": "cursor"}))
        self.assertEqual(self.cs.delegate_agents()[0], "cursor")
        (self.home / "config.json").write_text(json.dumps({"ttadk_worker_model": "deepseek-v4-pro-0813"}))
        self.assertNotIn("ttadk", self.cs.delegate_agents())  # that runs Claude Code, which doesn't report by command

    def test_session_start_records_the_id_and_hands_over_the_rules(self):
        self.workers(c={"status": "STARTING", "agent": "codex", "window": "@1", "dir": "/tmp", "task": "t"})
        self.cs.worker_dir("c").mkdir(parents=True, exist_ok=True)
        (self.cs.worker_dir("c") / "prompt.md").write_text("RULES")
        out = json.loads(self.hook(lambda: self.cs.hook_agent_start("codex"), {"session_id": "s-1"}, "c"))
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "RULES")
        self.assertEqual(self.cs.read_registry()[1]["c"]["session"], "s-1")
        self.assertEqual(self.hook(lambda: self.cs.hook_agent_start("codex"), {}, "nobody"), "")
        out = json.loads(self.hook(lambda: self.cs.hook_agent_start("cursor"), {"conversation_id": "s-2"}, "c"))
        self.assertEqual(out, {"additional_context": "RULES"})
        self.assertEqual(self.cs.read_registry()[1]["c"]["session"], "s-2")

    def test_stop_asks_for_a_report_once(self):
        self.workers(c={"status": "PROGRESS", "agent": "codex", "window": "@1", "dir": "/tmp", "task": "t"})
        d = self.cs.worker_dir("c")
        d.mkdir(parents=True, exist_ok=True)
        (d / "busy").write_text("x")
        stop = lambda: self.cs.hook_agent_stop("codex")
        out = json.loads(self.hook(stop, {"stop_hook_active": False}, "c"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("report c", out["reason"])
        self.assertTrue((d / "busy").exists())
        # asked once already: let it stop, mark it, tell the secretary
        self.assertEqual(json.loads(self.hook(stop, {"stop_hook_active": True}, "c")), {})
        self.assertTrue((d / "ended-unreported").exists())
        self.assertFalse((d / "busy").exists())
        self.assertEqual(len(self.alerts), 1)

    def test_cursor_stop_asks_three_times(self):
        self.workers(c={"status": "PROGRESS", "agent": "cursor", "window": "@1", "dir": "/tmp", "task": "t"})
        self.cs.worker_dir("c").mkdir(parents=True, exist_ok=True)
        stop = lambda: self.cs.hook_agent_stop("cursor")
        for n in range(3):
            out = json.loads(self.hook(stop, {"status": "completed", "loop_count": n}, "c"))
            self.assertIn("report c", out["followup_message"])
        self.assertEqual(self.hook(stop, {"status": "completed", "loop_count": 3}, "c"), "")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Cursor worker c", self.alerts[0])

    def test_stop_after_a_report_lets_it_end(self):
        self.workers(c={"status": "DONE", "agent": "codex", "window": "@1", "dir": "/tmp", "task": "t"})
        d = self.cs.worker_dir("c")
        d.mkdir(parents=True, exist_ok=True)
        (d / "reported").write_text("x")
        (d / "busy").write_text("x")
        stop = lambda: self.cs.hook_agent_stop("codex")
        self.assertEqual(json.loads(self.hook(stop, {}, "c")), {})
        self.assertFalse((d / "reported").exists())
        self.assertFalse((d / "busy").exists())
        self.assertFalse((d / "ended-unreported").exists())
        self.assertEqual(json.loads(self.hook(stop, {}, "nobody")), {})

    def test_dialogs_answered_and_not(self):
        sent = []
        self.cs.tmux = lambda *a, **k: sent.append(a)
        self.cs.time.sleep = lambda s: None
        codex = self.cs.AGENTS["codex"]
        trust = "Do you trust the contents of this directory?\n› 1. Yes, continue\n  2. No, quit"
        resume = "Choose working directory to resume this session\n› 1. Use session directory\n  2. Use current directory"
        hooks = "Hooks need review\n› 1. Review hooks\n  2. Trust all and continue\n  3. Continue without trusting"
        self.assertTrue(codex.answer_dialog("c", "@1", trust))
        self.assertTrue(codex.answer_dialog("c", "@1", resume))
        self.assertFalse(codex.answer_dialog("c", "@1", hooks))  # the user's to trust
        self.assertIn("Trust all and continue", codex.dialog_hint(hooks))
        self.assertEqual([a[-1] for a in sent], ["1", "2"])

    def delegate(self, *extra):
        self.workers(boss={"status": "PROGRESS", "dir": str(self.root), "task": "t"})
        got = []
        self.cs.cmd_spawn = lambda args: got.append(args.agent)
        out, saved_argv, saved_stdin = io.StringIO(), sys.argv, sys.stdin
        sys.argv = ["claudesec", "delegate", "boss", "--name", "j", "--dir", str(self.root), *extra]
        sys.stdin = io.StringIO("count the files")
        try:
            with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        return got

    def test_delegate_goes_to_the_first_available(self):
        self.on(ttadk=True, codex=True, cursor=True)
        self.assertEqual(self.delegate(), ["ttadk"])
        self.assertEqual(self.delegate("--agent", "cursor"), ["cursor"])
        self.assertEqual(self.delegate("--agent", "ttadk", "--model", "deepseek-v4-pro-0813"), [])  # Claude Code
        self.on(ttadk=False)
        self.assertEqual(self.delegate(), ["codex"])


if __name__ == "__main__":
    unittest.main()
