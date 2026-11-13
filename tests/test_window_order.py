"""The secretary's window is the first of its session, and a worker's window opened again comes back
where it was. 11-13 15:49 the secretary's window, opened again after a restart, came up at index 11
behind the workers and a window the user had opened; a worker switched to another model, or woken
after the secretary's exit, came back at the end. Against a real tmux server of its own (with the
user's renumber-windows on and base-index 1), so tmux's move-window -b / new-window -a semantics are
what is tested.

    python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import io
import itertools
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load

SESS = "claudesec"


class TmuxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.sock = str(self.home / "tmux.sock")
        self.cs = cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"tmux_session": SESS, "language": "en"}))
        self.registry({})
        self.moves = []

        def tmux(*a, check=True):
            if a[0] == "move-window":
                self.moves.append(a)
            return subprocess.run(["tmux", "-f", "/dev/null", "-S", self.sock, *a],
                                  capture_output=True, text=True, check=check).stdout
        cs.tmux = tmux
        cs.has_session = lambda name: subprocess.run(
            ["tmux", "-S", self.sock, "has-session", "-t", f"={name}"], capture_output=True).returncode == 0
        cs.tmux("new-session", "-d", "-s", SESS, "-n", "first", "-x", "200", "-y", "50", "sleep 600")
        cs.tmux("set-option", "-g", "base-index", "1")
        cs.tmux("set-option", "-g", "renumber-windows", "on")
        cs.tmux("move-window", "-s", f"={SESS}:0", "-t", f"={SESS}:1")
        self.moves.clear()

    def tearDown(self):
        subprocess.run(["tmux", "-S", self.sock, "kill-server"], capture_output=True)
        self.tmp.cleanup()

    def registry(self, workers):
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": workers}))

    def open(self, name, *target):
        return self.cs.tmux("new-window", "-d", *(target or ("-t", f"={SESS}:")), "-P", "-F", "#{window_id}",
                            "-n", name, "sleep 600").strip()

    def open_secretary(self):
        win = self.open("secretary")
        pane = self.cs.tmux("display-message", "-p", "-t", win, "#{pane_id}").strip()
        self.cs.tmux("set-option", "-p", "-t", pane, "@claudesec-role", "secretary")
        return win

    def secretary_alone(self):
        """The secretary's window, the session's only one."""
        sec = self.open_secretary()
        self.kill(f"={SESS}:first")
        return sec

    def names(self):
        return [(i, n) for i, _, n in self.cs.session_windows(SESS)]

    def kill(self, win):
        self.cs.tmux("kill-window", "-t", win)


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class WindowOrder(TmuxCase):

    def test_a_secretary_window_opened_behind_the_others_moves_to_the_front(self):
        self.cs.tmux("rename-window", "-t", f"={SESS}:1", "分析")
        self.open("rulecat"), self.open("claude-mod-audit")
        self.open_secretary()
        self.cs.keep_secretary_first(SESS)
        self.assertEqual(self.names(), [(1, "secretary"), (2, "分析"), (3, "rulecat"), (4, "claude-mod-audit")])

    def test_a_secretary_already_first_is_left_alone(self):
        sec = self.open_secretary()
        self.cs.tmux("move-window", "-b", "-s", sec, "-t", f"={SESS}:1")
        self.moves.clear()
        self.cs.keep_secretary_first(SESS)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.names()[0], (1, "secretary"))

    def test_the_secretary_takes_a_free_base_index(self):
        self.cs.tmux("set-option", "-g", "renumber-windows", "off")
        self.open("rulecat")
        self.kill(f"={SESS}:1")  # index 1 free now, rulecat at 2
        self.open_secretary()      # takes the first free index: 1 ...
        self.cs.tmux("move-window", "-s", f"={SESS}:secretary", "-t", f"={SESS}:5")  # ... unless it ran late
        self.cs.keep_secretary_first(SESS)
        self.assertEqual(self.names(), [(1, "secretary"), (2, "rulecat")])

    def test_dashboard_layout_puts_the_secretary_first(self):
        # what a client attaching or resizing runs, and every secretary start
        self.open("rulecat")
        self.open_secretary()
        self.cs.arrange_dashboard_locked = lambda sess: None
        self.cs.arrange_dashboard(SESS)
        self.assertEqual(self.names()[0][1], "secretary")

    def test_a_worker_woken_after_a_model_switch_comes_back_in_its_place(self):
        sec = self.secretary_alone()
        a, b = self.open("a"), self.open("b")
        self.open("分析")
        self.registry({"a": {}, "b": {}})
        place = self.cs.window_place(SESS, a)
        self.kill(a)
        self.open("a", *self.cs.window_target(SESS, place))
        self.assertEqual([n for _, n in self.names()], ["secretary", "a", "b", "分析"])

    def test_workers_woken_after_the_secretary_exits_come_back_in_order(self):
        # secretary, a, the user's window, b, c. The secretary's window goes first; shelve-all
        # notes where each worker is, then puts them away one by one; wake-all brings them back
        # together, in whatever order they come up.
        sec = self.secretary_alone()
        wins = {n: self.open(n) for n in ("a", "u", "b", "c")}
        self.registry({"a": {}, "b": {}, "c": {}})
        self.kill(sec)
        places = {n: self.cs.window_place(SESS, wins[n]) for n in ("a", "b", "c")}
        for n in ("a", "b", "c"):
            self.kill(wins[n])
        for order in itertools.permutations("abc"):
            with self.subTest(order=order):
                self.open_secretary()
                self.cs.keep_secretary_first(SESS)
                for n in order:
                    self.open(n, *self.cs.window_target(SESS, places[n]))
                self.assertEqual([n for _, n in self.names()], ["secretary", "a", "u", "b", "c"])
                for i, wid, n in self.cs.session_windows(SESS):
                    if n != "u":
                        self.kill(wid)

    def test_nothing_known_left_opens_at_the_end(self):
        self.registry({})
        place = {"before": [["@999", "gone"]], "after": [["@998", "zsh"]]}
        self.assertEqual(self.cs.window_target(SESS, place), ["-t", f"={SESS}:"])
        self.assertEqual(self.cs.window_target(SESS, None), ["-t", f"={SESS}:"])

    def test_a_name_shared_by_two_windows_is_no_neighbour(self):
        self.open("zsh"), self.open("zsh")
        self.registry({"zsh": {}})
        self.assertEqual(self.cs.window_target(SESS, {"before": [["@999", "zsh"]], "after": []}),
                         ["-t", f"={SESS}:"])


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class ShelveAndWakeKeepThePlace(TmuxCase):
    """cmd_shelve records the place; cmd_wake opens the window there."""
    def stub(self):
        cs = self.cs
        cs.conversation_exists = lambda w: True
        cs.where_now = lambda cwd: (cwd, None)
        cs.live_holder = lambda sid: None
        cs.follow_conversation = lambda *a, **k: None
        cs.ensure_name = lambda name, pane, sid: name
        cs.unfinished_progress = lambda name: None
        cs.last_turn = lambda sid: "summary"
        for obj in [*cs.AGENTS.values(), *cs.TTADK_RUNTIMES.values()]:
            obj.require = obj.room = obj.preflight = lambda *a, **k: None
            obj.confirm_resume = lambda name, pane, d, sid: ("ok", "SCREEN")
        real = cs.tmux
        cs.tmux = lambda *a, check=True: "" if a[0] == "respawn-pane" else real(*a, check=check)

    def test_shelve_then_wake(self):
        self.stub()
        sec = self.secretary_alone()
        a = self.open("a")
        self.open("分析")
        base = {"dir": str(self.home), "session": "sid", "task": "t", "status": "IDLE", "model": "claude-opus-5-5"}
        self.registry({"a": {**base, "window": a}})
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_shelve(argparse.Namespace(name="a", note=None, as_name=None, force=False, by="model"))
        w = self.cs.read_registry()[1]["a"]
        self.assertEqual(w["window_place"]["after"][0][1], "分析")
        self.assertEqual([n for _, n in self.names()], ["secretary", "分析"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_wake(argparse.Namespace(name="a", model=None, quiet=True, effort=None))
        self.assertEqual([n for _, n in self.names()], ["secretary", "a", "分析"])

    def test_shelve_all_notes_every_place_while_all_are_open(self):
        self.secretary_alone()
        wins = {n: self.open(n) for n in ("a", "b")}
        self.registry({n: {"status": "IDLE", "window": w} for n, w in wins.items()})
        seen = {}

        def shelve(args):
            seen[args.name] = args.place
            self.kill(wins[args.name])
            ws = json.loads((self.home / "registry.json").read_text())
            ws["workers"][args.name]["status"] = "SHELVED"
            (self.home / "registry.json").write_text(json.dumps(ws))
        cs = self.cs
        cs.cmd_shelve, cs.current_run, cs.secretary_alive = shelve, lambda: None, lambda: False
        cs.worker_busy = lambda name, w: False
        with contextlib.redirect_stdout(io.StringIO()):
            cs.shelve_all(1)
        self.assertEqual([n for _, n in seen["b"]["before"]], ["a", "secretary"])  # a closed first
        self.assertEqual([n for _, n in seen["a"]["after"]], ["b"])


if __name__ == "__main__":
    unittest.main()
