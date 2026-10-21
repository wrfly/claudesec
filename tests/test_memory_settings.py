"""Workers and the secretary read and write their own auto memory without a prompt: where the
memory dir's path runs through a symlink, its resolved path is named as the memory dir.

    python3 -m unittest discover -s tests
"""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


def mangle(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


class MemorySettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.cs = load(self.root / "state")
        self.config = self.root / "config"
        self.config.mkdir()
        self.saved_env = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)

    def tearDown(self):
        if self.saved_env is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved_env
        self.tmp.cleanup()

    def link_projects(self):
        """This machine's layout: <config>/projects is a link to another config dir's projects."""
        real = self.root / "dot-claude" / "projects"
        real.mkdir(parents=True)
        (self.config / "projects").symlink_to(real)
        return real

    def dir(self, *parts):
        d = self.root.joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_no_link_no_setting(self):
        (self.config / "projects").mkdir()
        self.assertEqual(self.cs.memory_settings(self.dir("proj")), {})

    def test_through_a_link_the_resolved_dir(self):
        real = self.link_projects()
        cwd = self.dir("proj")
        self.assertEqual(self.cs.memory_settings(cwd),
                         {"autoMemoryDirectory": str(real / mangle(cwd) / "memory")})

    def test_a_linked_config_dir(self):
        (self.config / "projects").mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root / "link")
        (self.root / "link").symlink_to(self.config)
        cwd = self.dir("proj")
        self.assertEqual(self.cs.memory_settings(cwd),
                         {"autoMemoryDirectory": str(self.config / "projects" / mangle(cwd) / "memory")})

    def test_worktrees_and_subdirectories_use_the_main_checkout(self):
        real = self.link_projects()
        repo = self.dir("repo")
        git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        git("worktree", "add", "-q", str(repo / ".claude" / "worktrees" / "wt"))
        want = {"autoMemoryDirectory": str(real / mangle(repo) / "memory")}
        for cwd in (repo, self.dir("repo", "sub"), repo / ".claude" / "worktrees" / "wt"):
            self.assertEqual(self.cs.memory_settings(cwd), want, cwd)

    def test_left_alone_where_it_cant_be_worked_out(self):
        self.link_projects()
        bare = self.dir("bare.git")
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        self.assertEqual(self.cs.memory_settings(bare), {})
        self.assertEqual(self.cs.memory_settings(self.dir("x" * 210)), {})  # Claude Code hashes long names

    def test_worker_settings(self):
        real = self.link_projects()
        cwd = self.dir("proj")
        cfg = {"secretary_name": "secretary"}
        settings = lambda d: json.loads((d / "settings.json").read_text())
        s = settings(self.cs.prepare_worker("w", cfg, "hi", cwd))
        self.assertEqual(s["autoMemoryDirectory"], str(real / mangle(cwd) / "memory"))
        self.assertIn("SessionStart", s["hooks"])
        # the review worker works on the secretary's memory instead of its own
        s = settings(self.cs.prepare_worker("r", cfg, "hi", cwd, memory="/elsewhere/memory"))
        self.assertEqual(s["autoMemoryDirectory"], "/elsewhere/memory")


if __name__ == "__main__":
    unittest.main()
