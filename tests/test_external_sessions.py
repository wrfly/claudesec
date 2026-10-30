"""Which live Claude sessions claudesec lists as not its own (and offers to adopt).

    python3 -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "claudesec"


def load(home):
    os.environ["CLAUDESEC_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader("claudesec_under_test", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    # An external agent's hooks file is global (~/.cursor/hooks.json, ~/.codex/hooks.json): a test
    # that spawns or wakes one would point the user's real one at this checkout.
    for cls in vars(mod).values():
        if isinstance(cls, type) and issubclass(cls, mod.Agent):
            cls.hooks_file = lambda self, h=Path(home): h / "agent-hooks" / f"{self.protocol}.json"
    return mod


class ExternalSessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"tmux_session": "claudesec"}))
        (self.home / "secretary.session").write_text("sec-sid\n")
        self.workers = {}
        self.live = []
        # The tmux server: claudesec's session holds the secretary (%1) and a worker (%2);
        # the user's own session "0" holds %9.
        self.panes = {"claudesec": ["%1", "%2"], "0": ["%9"]}
        self.cs.claude_sessions = lambda: [dict(s) for s in self.live]
        self.cs.tmux = self.fake_tmux

    def tearDown(self):
        self.tmp.cleanup()

    def fake_tmux(self, *args, check=True):
        if args[:2] == ("list-panes", "-s"):
            target = args[args.index("-t") + 1].strip("=:")
            return "\n".join(self.panes.get(target, [])) + "\n"
        raise AssertionError(f"unexpected tmux call {args}")

    def registry(self, **workers):
        self.workers = workers
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": workers}))

    def session(self, name, sid, where, kind="interactive"):
        self.live.append({"pid": len(self.live) + 100, "name": name, "sessionId": sid,
                          "kind": kind, "tmux": where, "startedAt": len(self.live)})

    def external(self):
        return [s["name"] for s in self.cs.external_sessions()]

    def test_worker_being_woken_under_an_automatic_name(self):
        # wake-all: the registry has the window, no session yet; Claude Code named it on its own.
        self.registry(storage={"status": "STARTING", "session": None, "window": "@21"})
        self.session("agentlodge-da", "new-sid", "claudesec:@21.%2")
        self.assertEqual(self.external(), [])

    def test_worker_window_outside_claudesec_session(self):
        # spawned while claudesec's tmux session didn't exist: only the window id ties it to us
        self.registry(w={"status": "STARTING", "session": None, "window": "@30"})
        self.session("auto-name", "new-sid", "0:@30.%9")
        self.assertEqual(self.external(), [])

    def test_anything_in_claudesec_tmux_session(self):
        self.registry()
        self.session("someone", "x-sid", "claudesec:@40.%2")
        self.assertEqual(self.external(), [])

    def test_secretary_by_session_id_wherever_it_runs(self):
        self.registry()
        self.session("renamed", "sec-sid", None)
        self.assertEqual(self.external(), [])

    def test_worker_by_session_id(self):
        self.registry(web={"status": "DONE", "session": "web-sid", "window": "@24"})
        self.session("whatever", "web-sid", "0:@50.%9")
        self.assertEqual(self.external(), [])

    def test_stranger_sharing_a_worker_name_is_external(self):
        self.registry(web={"status": "DONE", "session": "web-sid", "window": "@24"})
        self.session("web", "other-sid", "0:@4.%9")
        self.session("secretary", "other-sid-2", None)
        self.assertEqual(self.external(), ["web", "secretary"])

    def test_user_session_outside_tmux_and_elsewhere(self):
        self.registry()
        self.session("blog", "b-sid", "0:@4.%9")
        self.session("notes", "n-sid", None)
        self.assertEqual(self.external(), ["blog", "notes"])

    def test_no_claudesec_tmux_session(self):
        self.panes = {"0": ["%9"]}
        self.registry()
        self.session("blog", "b-sid", "0:@4.%9")
        self.assertEqual(self.external(), ["blog"])


if __name__ == "__main__":
    unittest.main()
