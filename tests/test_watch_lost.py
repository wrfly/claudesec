"""Alerts reach an idle secretary even when nothing will deliver them: 11-12 night, a turn cut
short by the safeguards hung busy for eleven hours, no Stop hook ran, no watcher was armed, and
the night's reminders waited in alerts/ until the user spoke. The reminder checker presses Esc on
a turn that has hung, and types a line into an idle secretary's window when alerts wait with no
watcher, whose turn end arms one. "Don't disturb me" doesn't hold that back.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from test_external_sessions import load

NOW = 1794538000.0  # 11-13 10:46:40 +08:00


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class WatchLost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "secretary.session").write_text("sec\n")
        self.session = {"sessionId": "sec", "status": "idle", "statusUpdatedAt": (NOW - 3600) * 1000,
                        "tmux": "claudesec:@0.%0"}
        self.cs.claude_sessions = lambda: [self.session]
        self.cs.claude_dir = lambda: self.home / "cc"
        self.conv = self.home / "cc" / "projects" / "-x" / "sec.jsonl"
        self.conv.parent.mkdir(parents=True)
        self.conv.write_text("")
        self.keys = []
        self.cs.tmux = lambda *a, check=True: self.keys.append(a) or ""
        self.screen = "● done\n────\n❯ \n────\n  ⏵⏵ auto mode on"
        self.cs.pane_text = lambda target, suggestion="mark": self.screen
        self.cs.watcher_armed = lambda: False
        self.alerts = self.home / "alerts"
        self.alerts.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def alert(self, text, at):
        f = self.alerts / f"{int(at * 1e9)}.txt"
        f.write_text(text + "\n")
        os.utime(f, (at, at))

    def entries(self, *rows):
        with self.conv.open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def typed(self):
        return [a for a in self.keys if a[:1] == ("send-keys",) and "-l" in a]

    def escapes(self):
        return [a for a in self.keys if a == ("send-keys", "-t", "%0", "Escape")]

    def test_idle_with_alerts_and_no_watcher_types_a_line_to_rearm_it(self):
        self.alert("[定时任务 r82] …", NOW - 300)
        self.assertEqual(self.cs.check_watch(NOW), "typed")
        (line,) = self.typed()
        self.assertEqual(line[:3], ("send-keys", "-t", "%0"))
        self.assertTrue(line[-1].startswith("[claudesec]"))
        self.assertTrue(line[-1].startswith(self.cs.NOT_USER))  # not taken for the user speaking
        self.assertEqual(self.keys[-1], ("send-keys", "-t", "%0", "Enter"))
        self.assertTrue(list(self.alerts.glob("*.txt")))  # left for the watcher that turn arms

    def test_not_while_a_watcher_is_armed_or_the_alert_is_new(self):
        self.alert("a", NOW - 30)
        self.assertIsNone(self.cs.check_watch(NOW))  # it may be on its way already
        self.alert("b", NOW - 300)
        self.cs.watcher_armed = lambda: True
        self.assertIsNone(self.cs.check_watch(NOW))
        self.assertEqual(self.keys, [])

    def test_not_over_a_draft_the_user_is_typing(self):
        self.alert("a", NOW - 300)
        self.screen = "────\n❯ 帮我看一下\n────"
        self.assertIsNone(self.cs.check_watch(NOW))
        self.assertEqual(self.keys, [])

    def test_once_then_again_only_after_a_while(self):
        self.alert("a", NOW - 300)
        self.assertEqual(self.cs.check_watch(NOW), "typed")
        self.assertIsNone(self.cs.check_watch(NOW + 60))
        self.assertEqual(self.cs.check_watch(NOW + self.cs.RESCUE_RETRY_SECS + 1), "typed")
        self.assertEqual(len(self.typed()), 2)

    def test_dont_disturb_doesnt_hold_it_back(self):
        (self.home / self.cs.DND).write_text("on")
        self.alert("a", NOW - 300)
        self.assertEqual(self.cs.check_watch(NOW), "typed")

    def test_the_night_of_11_12_a_hung_turn_gets_an_esc_then_its_alerts(self):
        start = NOW - 11 * 3600
        self.entries({"type": "user", "timestamp": iso(start), "message": {"content": "[委托回执] …"}},
                     {"type": "api-request", "timestamp": iso(start + 3)},
                     {"type": "system", "subtype": "informational", "timestamp": iso(start + 3),
                      "content": "safeguards stopped the response above · continuing once"},
                     {"type": "api-request", "timestamp": iso(start + 4)},
                     *({"type": "queue-operation", "operation": "enqueue", "timestamp": iso(start + 6000 + i),
                        "content": "<cross-session-message from-name=\"oncall\">TYPE: DONE"} for i in range(5)))
        self.session.update(status="busy", statusUpdatedAt=start * 1000)
        for i, h in enumerate((0.5, 2.5, 4.5, 6.5, 8.5)):
            self.alert(f"[定时任务 r82] #{i}", start + h * 3600)
        self.assertEqual(self.cs.check_watch(NOW), "escaped")
        self.assertEqual(len(self.escapes()), 1)
        note = [f.read_text() for f in self.alerts.glob("*.txt") if "卡住" in f.read_text()]
        self.assertEqual(len(note), 1)
        self.assertIn("659 分钟", note[0])
        self.assertIsNone(self.cs.check_watch(NOW + 15))  # still busy: not Esc after Esc
        self.assertEqual(self.cs.check_watch(NOW + self.cs.RESCUE_RETRY_SECS), "escaped")  # it didn't take: again
        self.assertEqual(sum("卡住" in f.read_text() for f in self.alerts.glob("*.txt")), 1)  # one note for the turn
        # Esc runs no Stop hook: the secretary is at its prompt, and no watcher
        self.session.update(status="idle", statusUpdatedAt=(NOW + 20) * 1000)
        self.assertIsNone(self.cs.check_watch(NOW + 60))  # just stopped: a Stop hook may be arming one
        self.assertEqual(self.cs.check_watch(NOW + 20 + self.cs.WATCH_LOST_SECS), "typed")

    def test_a_queued_message_is_enough_to_cut_a_hung_turn(self):
        start = NOW - 3600
        self.entries({"type": "api-request", "timestamp": iso(start)},
                     {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(start + 600)})
        self.session.update(status="busy", statusUpdatedAt=start * 1000)
        self.assertEqual(self.cs.check_watch(NOW), "escaped")

    def test_a_long_turn_that_is_working_is_left_alone(self):
        start = NOW - 3 * 3600
        self.session.update(status="busy", statusUpdatedAt=start * 1000)
        self.alert("a", NOW - 1800)
        self.entries({"type": "user", "timestamp": iso(start)},
                     {"type": "assistant", "timestamp": iso(NOW - 120)})  # wrote two minutes ago
        self.assertIsNone(self.cs.check_watch(NOW))
        # quiet in its own transcript, but a foreground subagent is working
        self.conv.write_text(json.dumps({"type": "user", "timestamp": iso(start)}) + "\n")
        sub = self.conv.parent / "sec" / "subagents" / "agent-1.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text("{}\n")
        os.utime(sub, (NOW - 60, NOW - 60))
        self.assertIsNone(self.cs.check_watch(NOW))
        self.assertEqual(self.keys, [])

    def test_a_hung_turn_with_nothing_waiting_is_left_alone(self):
        start = NOW - 3600
        self.entries({"type": "api-request", "timestamp": iso(start)})
        self.session.update(status="busy", statusUpdatedAt=start * 1000)
        self.assertIsNone(self.cs.check_watch(NOW))
        self.assertEqual(self.keys, [])

    def test_a_dialog_is_not_a_hung_turn(self):
        start = NOW - 3600
        self.entries({"type": "api-request", "timestamp": iso(start)})
        self.session.update(status="waiting", statusUpdatedAt=start * 1000)
        self.alert("a", NOW - 600)
        self.assertIsNone(self.cs.check_watch(NOW))

    def test_the_watcher_says_where_it_is(self):
        load_real = load(self.home)  # the real watcher_armed
        load_real.claude_sessions = lambda: []
        self.alert("a", NOW)
        saved, err = sys.stdin, io.StringIO()
        sys.stdin = io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "s"}))
        try:
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                load_real.hook_secretary_watch()
        finally:
            sys.stdin = saved
        self.assertEqual((self.home / "watch.pid").read_text(), str(os.getpid()))
        self.assertFalse(load_real.watcher_armed())  # alive, but not a secretary-watch
        (self.home / "watch.pid").write_text("999999999")
        self.assertFalse(load_real.watcher_armed())


class CheckerRestart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cs = load(Path(self.tmp.name))
        self.execs = []
        patch = mock.patch.object(self.cs.os, "execv", lambda path, argv: self.execs.append(argv))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_restarts_on_new_code_but_not_on_broken_code(self):
        stamp = self.cs.code_stamp()
        self.assertEqual(self.cs.restart_if_updated(stamp, 42), stamp)
        self.assertEqual(self.execs, [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.restart_if_updated(stamp - 1, 42)
        self.assertEqual(self.execs, [[sys.executable, str(self.cs.SELF), "lifecycle", "reminders", "42"]])
        self.execs.clear()
        self.cs.SELF = Path(self.tmp.name) / "claudesec"
        self.cs.SELF.write_text("def broken(:\n")
        self.assertEqual(self.cs.restart_if_updated(0, 42), self.cs.code_stamp())
        self.assertEqual(self.execs, [])


if __name__ == "__main__":
    unittest.main()
