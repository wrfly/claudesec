"""Config spawn_agent / spawn_model: what `spawn` starts without --agent (the user, 2026-11-11:
dispatch only on ttadk claude + deepseek-v4.1-flash). Unset, Claude Code on worker_model as
before; a --model of Claude's still runs plain Claude Code. review_agent / review_model: what
`claudesec review` starts; unset, as spawn without --agent.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_external_sessions import load


class Opened(Exception):
    pass


class SpawnDefaultAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "state"
        self.home.mkdir()
        self.cs = load(self.home)
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {}}))
        for obj in [*self.cs.AGENTS.values(), *self.cs.TTADK_RUNTIMES.values()]:
            obj.require = obj.room = lambda *a, **k: None
            obj.preflight = lambda *a, **k: None
        self.opened = []

        def tmux(*a, **k):
            if a and a[0] == "new-window":
                self.opened.append(a[-1])
                raise Opened()
            return ""
        self.cs.tmux = tmux
        self.cs.alive_windows = lambda: set()

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, **cfg):
        (self.home / "config.json").write_text(json.dumps({"language": "en", **cfg}))

    def spawn(self, agent=None, model=None):
        self.opened.clear()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Opened):
            self.cs.cmd_spawn(SimpleNamespace(name="w", dir=str(self.home), model=model, discuss=False, agent=agent,
                                              for_worker=None, budget=None, task="t"))
        return self.opened[0]

    def test_unset_is_claude_code_on_worker_model(self):
        self.config(worker_model="claude-opus-5-5")
        cmd = self.spawn()
        self.assertIn("--model claude-opus-5-5", cmd)
        self.assertNotIn("ttadk", cmd)

    def test_set_runs_its_agent_and_model(self):
        self.config(spawn_agent="ttadk", spawn_model="deepseek-v4.1-flash")
        cmd = self.spawn()
        self.assertIn("ttadk", cmd)
        self.assertIn("deepseek-v4.1-flash", cmd)

    def test_a_claude_model_asked_for_is_still_claude_code(self):
        self.config(spawn_agent="ttadk", spawn_model="deepseek-v4.1-flash")
        cmd = self.spawn(model="claude-opus-5-5[1m]")
        self.assertIn("claude-opus-5-5[1m]", cmd)
        self.assertNotIn("ttadk", cmd)
        self.assertIn("glm-5.3", self.spawn(model="glm-5.3"))  # another non-Claude one: through spawn_agent

    def test_agent_named_wins(self):
        self.config(spawn_agent="ttadk", spawn_model="deepseek-v4.1-flash", worker_model="claude-opus-5-5")
        self.assertNotIn("ttadk", self.spawn(agent="claude"))

    def test_review_uses_its_own_or_spawns(self):
        seen = []
        self.cs.cmd_spawn = lambda a: seen.append((a.agent, a.model))
        self.cs.secretary_memory_dir = lambda: self.home / "mem"
        self.config()
        self.cs.cmd_review(SimpleNamespace(print=False))
        self.config(review_agent="ttadk", review_model="deepseek-v4.1-flash")
        self.cs.cmd_review(SimpleNamespace(print=False))
        self.assertEqual(seen, [(None, None), ("ttadk", "deepseek-v4.1-flash")])


if __name__ == "__main__":
    unittest.main()
