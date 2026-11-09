"""What a worker left running is found by its name too, not only by CLAUDE_CODE_SESSION_ID:
modelaudit's headless Chrome (2026-11-09) was started under `env -i`, so shelve and close
missed it and it ran 8 hours. shelve and close refuse on it, the watchdog alerts on it.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_external_sessions import load


def proc(argv, cwd="/", env=None, ppid=100, start=1000.0, comm=None):
    return SimpleNamespace(ppid=ppid, comm=comm or Path(argv[0]).name, argv=argv, cwd=cwd, env=env or {},
                           start=start, rss=2**20)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))
        self.cs.tmux = lambda *a, **k: ""
        self.cs.claude_sessions = lambda: []

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, snap, name="w", sid=None, since=None, keep=(), containers=()):
        return self.cs.leftovers(name, sid=sid, since=since, snap=snap, containers=list(containers), keep=set(keep))


class Marks(Base):
    def test_env_stripped_browser_is_found_by_its_dirs(self):
        snap = {10: proc(["/usr/bin/google-chrome", "--headless=new", "--user-data-dir=/tmp/claude-w/chrome"],
                         cwd="/tmp/claude-w", env={"HOME": "/tmp/claude-w"}, ppid=1),
                11: proc(["/opt/google/chrome/chrome", "--type=zygote"], cwd="/tmp/claude-w", ppid=10)}
        found = self.find(snap)
        self.assertEqual([(f["id"], f["sure"], f["n"]) for f in found], [(10, True, 2)])

    def test_each_mark(self):
        work = str(self.home / "work" / "w")
        for p in (proc(["python3", "x.py"], cwd=work + "/sub"),
                  proc(["python3", f"{work}/x.py"]),
                  proc(["node", "srv.js", "--cache=/tmp/claude-w/c"]),
                  proc(["sleep", "9"], env={"TMPDIR": "/tmp/claude-w"}),
                  proc(["sleep", "9"], env={"CLAUDE_CODE_SESSION_ID": "sid"})):
            self.assertEqual(len(self.find({10: p}, sid="sid")), 1, p.argv)

    def test_not_another_workers_or_a_mention_in_text(self):
        for p in (proc(["chrome", "--user-data-dir=/tmp/claude-w2/chrome"], cwd="/tmp/claude-w2"),
                  proc(["python3", str(self.home / "work" / "w2" / "x.py")]),
                  # a worker's own claude: its whole task, with other workers' paths, in one argument
                  proc(["claude", "--resume", "s", "kill the chrome in /tmp/claude-w/chrome please"]),
                  proc(["sleep", "9"], env={"CLAUDE_CODE_SESSION_ID": "other"}),
                  proc(["vim", "notes"], cwd="/home/u")):
            self.assertEqual(self.find({10: p}, sid="sid"), [], p.argv)

    def test_live_sessions_are_not_leftovers(self):
        snap = {10: proc(["sleep", "9"], cwd="/tmp/claude-w")}
        self.assertEqual(self.find(snap, keep={10}), [])
        self.cs.tmux = lambda *a, **k: "5\n"
        snap = {5: proc(["zsh"], ppid=1), 6: proc(["claude"], ppid=5), 10: proc(["sleep", "9"], cwd="/tmp/claude-w", ppid=6)}
        self.assertIn(10, self.cs.live_trees(snap))
        self.assertNotIn(5, self.cs.live_trees({**snap, 5: proc(["zsh"], ppid=1), 1: proc(["init"], ppid=0)}) - {5, 6, 10})

    def test_init_above_this_command_does_not_cover_everything(self):
        snap = {1: proc(["init"], ppid=0), os.getpid(): proc(["python3"], ppid=1),
                10: proc(["sleep", "9"], cwd="/tmp/claude-w", ppid=1)}
        self.assertNotIn(10, self.cs.live_trees(snap))
        self.assertEqual(len(self.cs.leftovers("w", snap=snap, containers=[])), 1)

    def test_tmp_name_is_only_maybe(self):
        found = self.find({10: proc(["chrome", "--headless=new", "--user-data-dir=/tmp/w/p"], cwd="/tmp/w", ppid=1)})
        self.assertEqual([f["sure"] for f in found], [False])

    def test_orphan_headless_browser_while_it_ran_is_maybe(self):
        systemd = proc(["/usr/lib/systemd/systemd", "--user"], ppid=1, comm="systemd")
        chrome = proc(["/opt/google/chrome/chrome", "--headless=new", "--user-data-dir=/tmp/x"], ppid=2, start=2000)
        self.assertEqual([f["sure"] for f in self.find({2: systemd, 10: chrome}, since=1500)], [False])
        self.assertEqual(self.find({2: systemd, 10: chrome}, since=2500), [])  # started before it
        shell = proc(["zsh"], ppid=1)
        self.assertEqual(self.find({2: shell, 10: chrome}, since=1500), [])  # someone's shell still has it
        windowed = proc(["/opt/google/chrome/chrome"], ppid=2, start=2000)
        self.assertEqual(self.find({2: systemd, 10: windowed}, since=1500), [])  # the user's own browser

    def test_containers(self):
        def c(name, labels=None, mounts=(), created=2000):
            return {"id": name[:12], "name": name, "labels": labels or {}, "mounts": list(mounts), "created": created}
        work = str(self.home / "work" / "w")
        hit = [c("a", {"claudesec.worker": "w"}), c("b", mounts=[work + "/data"]),
               c("c", {"com.docker.compose.project.working_dir": work}), c("w-db"), c("claude-w-pg")]
        miss = [c("vibrant_kapitsa"), c("w2-db"), c("db", {"claudesec.worker": "w2"}),
                c("w", created=500)]  # the user's own `w`, there before the worker
        found = self.find({}, since=1000, containers=hit + miss)
        self.assertEqual(sorted(f["what"] for f in found), ["a", "b", "c", "claude-w-pg", "w-db"])
        self.assertEqual(self.find({}, since=None, containers=[c("w-db")]), [])  # a name alone needs its start

    def test_a_real_env_stripped_process(self):
        work = self.home / "work" / "w"
        work.mkdir(parents=True)
        p = subprocess.Popen(["env", "-i", f"HOME={work}", "sleep", "30"], cwd=work)
        try:
            for _ in range(50):
                if Path(f"/proc/{p.pid}/cmdline").read_bytes().startswith(b"sleep"):
                    break
                time.sleep(0.02)
            snap = self.cs.ps_snapshot()
            self.assertNotIn("CLAUDE_CODE_SESSION_ID", snap[p.pid].env)
            found = self.cs.leftovers("w", snap=snap, containers=[], keep=set())
            self.assertEqual([f["id"] for f in found], [p.pid])
            self.cs.kill_leftovers(found)
            self.assertEqual(p.wait(timeout=5), -15)
        finally:
            p.kill()
            p.wait()


class Commands(Base):
    def setUp(self):
        super().setUp()
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "w": {"status": "DONE", "task": "t", "dir": str(self.home), "session": "sid", "window": None,
                  "model": "m", "started_at": "2026-11-09T10:00:00+08:00", "archived": "x.md"}}}))
        (self.home / "workers" / "w").mkdir(parents=True)
        self.left = [{"kind": "proc", "id": 10, "what": "chrome --headless", "why": "cwd /tmp/claude-w",
                      "sure": True, "n": 12, "rss": 900 * 2**20}]
        self.cs.worker_leftovers = lambda name, w: list(self.left)
        self.killed = []
        self.cs.kill_leftovers = lambda found: self.killed.extend(found) or [str(f["id"]) for f in found if f["sure"]]

    def close(self, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cs.cmd_close(SimpleNamespace(**{"name": "w", "kill_leftovers": False, "force": False, **kw}))
        return out.getvalue()

    def workers(self):
        return json.loads((self.home / "registry.json").read_text())["workers"]

    def test_close_refuses_and_shows_them(self):
        with self.assertRaises(SystemExit) as e:
            self.close()
        self.assertIn("chrome --headless", str(e.exception))
        self.assertIn("--kill-leftovers", str(e.exception))
        self.assertIn("w", self.workers())
        self.assertEqual(self.killed, [])

    def test_close_force_leaves_them_and_says_so(self):
        out = self.close(force=True)
        self.assertNotIn("w", self.workers())
        self.assertIn("still running", out)
        self.assertEqual(self.killed, [])

    def test_close_kill_leftovers(self):
        out = self.close(kill_leftovers=True)
        self.assertNotIn("w", self.workers())
        self.assertEqual([f["id"] for f in self.killed], [10])
        self.assertNotIn("still running", out)

    def test_close_with_only_unsure_ones_closes(self):
        self.left[0]["sure"] = False
        out = self.close()
        self.assertNotIn("w", self.workers())
        self.assertIn("not sure", out)

    def test_shelve_refuses(self):
        self.cs.conversation_exists = lambda w: True
        self.cs.live_holder = lambda sid: None
        args = SimpleNamespace(name="w", note="n", as_name=None, force=False)
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_shelve(args)
        self.assertIn("Not shelved", str(e.exception))
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_shelve(SimpleNamespace(name="w", note="n", as_name=None, force=False, by="exit"))
        self.assertEqual(self.workers()["w"]["status"], "SHELVED")

    def test_watchdog_alerts_once_and_not_for_a_running_worker(self):
        snap = {10: proc(["chrome", "--headless=new"], cwd="/tmp/claude-w", ppid=1)}
        self.cs.ps_snapshot = lambda: snap
        self.cs.docker_containers = lambda: []
        self.cs.alive_windows = lambda: {"@1"}
        self.assertEqual(len(self.cs.check_leftovers()), 1)
        self.assertEqual(self.cs.check_leftovers(), [])
        self.assertIn("claudesec leftovers w --kill", next((self.home / "alerts").iterdir()).read_text())
        workers = self.workers()
        workers["w"].update(status="PROGRESS", window="@1")
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": workers}))
        (self.home / "leftovers.json").unlink()
        self.assertEqual(self.cs.check_leftovers(), [])

    def test_watchdog_finds_closed_workers_and_stray_browsers(self):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {}}))
        systemd = proc(["/usr/lib/systemd/systemd", "--user"], ppid=1, comm="systemd")
        snap = {2: systemd, 10: proc(["sleep", "1d"], cwd="/tmp/claude-w", ppid=2),
                11: proc(["/opt/google/chrome/chrome", "--headless=new", "--user-data-dir=/tmp/x"], ppid=2)}
        self.cs.ps_snapshot = lambda: snap
        self.cs.docker_containers = lambda: []
        self.cs.alive_windows = lambda: set()
        raised = self.cs.check_leftovers()
        self.assertEqual(len(raised), 2)
        self.assertIn("[leftovers] w isn't running", raised[0])
        self.assertIn("headless browsers nobody owns", raised[1])


if __name__ == "__main__":
    unittest.main()
