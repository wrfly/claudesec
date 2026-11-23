"""A full disk cannot silently kill the checker or consume an undelivered reminder.

    python3 -m unittest discover -s tests
"""
import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_external_sessions import load


def full_disk():
    return OSError(errno.ENOSPC, "No space left on device")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cs = load(self.home)
        (self.home / "config.json").write_text(json.dumps({"language": "en"}))

    def tearDown(self):
        self.tmp.cleanup()


class Checker(Base):
    def setUp(self):
        super().setUp()
        for name in ("fire_due_reminders", "check_watch", "check_stuck", "check_runaway",
                     "check_stale", "check_leftovers", "check_idle"):
            setattr(self.cs, name, mock.Mock(return_value=None))
        self.alerts = []
        self.cs.raise_alert = self.alerts.append

    def rounds(self, count):
        self.cs.current_run = mock.Mock(side_effect=[42] * count + [None])
        with mock.patch.object(self.cs.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.cs.check_reminders(42)

    def test_reminder_failure_does_not_stop_other_checks_and_is_retried(self):
        self.cs.fire_due_reminders.side_effect = [full_disk(), None]
        self.rounds(2)
        for name in ("fire_due_reminders", "check_watch", "check_stuck", "check_idle"):
            self.assertEqual(getattr(self.cs, name).call_count, 2, name)
        self.assertTrue(any("reminders failed" in a and "No space" in a for a in self.alerts))
        self.assertTrue(any("reminders recovered" in a for a in self.alerts))
        state = json.loads((self.home / self.cs.CHECKER_STATE).read_text())
        self.assertEqual((state["run_pid"], state["errors"]), (42, {}))

    def test_heartbeat_and_alert_failures_survive_in_memory(self):
        original = Path.write_text
        failed = False

        def write(path, *a, **kw):
            nonlocal failed
            if path.name.startswith("reminders-checker.") and path.suffix == ".tmp" and not failed:
                failed = True
                raise full_disk()
            return original(path, *a, **kw)

        def alert(text):
            if len(self.alerts) == 0 and not self.cs.check_idle.call_count > 1:
                raise full_disk()
            self.alerts.append(text)

        self.cs.fire_due_reminders.side_effect = [full_disk(), None, None]
        self.cs.raise_alert = alert
        with mock.patch.object(Path, "write_text", write):
            self.rounds(3)
        self.assertEqual(self.cs.check_idle.call_count, 3)
        self.assertTrue(any("reminders failed" in a and "reminders recovered" in a for a in self.alerts))
        self.assertTrue(any("heartbeat failed" in a and "heartbeat recovered" in a for a in self.alerts))
        self.assertEqual(json.loads((self.home / self.cs.CHECKER_STATE).read_text())["errors"], {})
        self.assertFalse(list(self.home.glob("reminders-checker.*.tmp")))

    def test_less_frequent_failed_checks_are_retried_next_round(self):
        self.cs.check_stale.side_effect = [full_disk(), None]
        self.cs.check_runaway.side_effect = [full_disk(), None]
        self.cs.check_leftovers.side_effect = [full_disk(), None]
        self.rounds(2)
        for name in ("check_stale", "check_runaway", "check_leftovers"):
            self.assertEqual(getattr(self.cs, name).call_count, 2)

    def test_stale_marker_unlink_failure_does_not_escape_the_loop(self):
        marker = self.home / "stale-due"
        marker.touch()
        original = Path.unlink
        failed = False

        def unlink(path, *a, **kw):
            nonlocal failed
            if path == marker and not failed:
                failed = True
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "unlink", unlink):
            self.rounds(2)
        self.assertEqual(self.cs.check_idle.call_count, 2)
        self.assertFalse(marker.exists())

    def test_log_write_failure_is_nonfatal(self):
        out = mock.Mock()
        out.write.side_effect = full_disk()
        with mock.patch.object(self.cs.sys, "stdout", out):
            self.cs.lifecycle_note("still running")

    def test_no_run_pid_does_not_create_an_unowned_checker(self):
        self.cs.current_run = mock.Mock(return_value=None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.check_reminders(None)
        self.cs.fire_due_reminders.assert_not_called()

    def test_reported_errors_do_not_prevent_hot_reload_of_a_fix(self):
        self.cs.fire_due_reminders.side_effect = full_disk()
        self.cs.restart_if_updated = mock.Mock(return_value=self.cs.code_stamp())
        self.rounds(2)
        self.assertEqual(self.cs.restart_if_updated.call_count, 2)

    def test_unreported_in_memory_fault_is_not_discarded_by_hot_reload(self):
        self.cs.fire_due_reminders.side_effect = full_disk()
        self.cs.raise_alert = mock.Mock(side_effect=full_disk())
        self.cs.restart_if_updated = mock.Mock(return_value=self.cs.code_stamp())
        self.rounds(2)
        self.assertEqual(self.cs.restart_if_updated.call_count, 1)


class Health(Base):
    def setUp(self):
        super().setUp()
        self.cs.current_run = lambda: 42
        self.real_detach = self.cs.detach
        self.cs.detach = mock.Mock()

    def state(self, **kw):
        data = {"pid": 123, "run_pid": 42, "updated_at": self.cs.now(), "errors": {}}
        data.update(kw)
        (self.home / self.cs.CHECKER_STATE).write_text(json.dumps(data))

    def test_real_lock_is_authoritative_not_a_stale_pid_file(self):
        self.state()
        self.assertFalse(self.cs.reminder_checker_running())
        with (self.home / "reminders-checker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertTrue(self.cs.reminder_checker_running())
        self.assertFalse(self.cs.reminder_checker_running())

    def test_healthy_is_quiet_and_does_not_start_anything(self):
        self.cs.reminder_checker_running = lambda: True
        self.state()
        self.assertIsNone(self.cs.ensure_reminder_checker())
        self.cs.detach.assert_not_called()

    def test_dead_checker_restarts_only_reminders_for_the_original_run(self):
        self.cs.reminder_checker_running = mock.Mock(side_effect=[False, True])
        self.cs.detach.return_value = SimpleNamespace(pid=123, poll=lambda: None)
        note = self.cs.ensure_reminder_checker()
        self.assertIn("restarted only it", note)
        self.cs.detach.assert_called_once_with("reminders", run_pid=42)

    def test_restart_failure_is_visible_not_a_false_success(self):
        self.cs.reminder_checker_running = lambda: False
        self.cs.detach.return_value = SimpleNamespace(pid=123, poll=lambda: 1)
        self.assertIn("did not acquire its lock", self.cs.ensure_reminder_checker())

    def test_missing_run_never_wakes_or_shelves_workers(self):
        self.cs.current_run = lambda: None
        self.assertIn("No live claudesec run", self.cs.ensure_reminder_checker())
        self.cs.detach.assert_not_called()

    def test_concurrent_supervisor_hook_does_not_start_a_second_checker(self):
        with (self.home / "reminders-supervisor.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertIsNone(self.cs.ensure_reminder_checker())
        self.cs.detach.assert_not_called()

    def test_stale_or_failed_heartbeat_is_visible_without_killing_it(self):
        self.cs.reminder_checker_running = lambda: True
        self.assertIn("unavailable", self.cs.ensure_reminder_checker())
        self.state(updated_at=(dt.datetime.now().astimezone() - dt.timedelta(minutes=5)).isoformat())
        self.assertIn("stale", self.cs.ensure_reminder_checker())
        self.state(errors={"reminders": {"error": "ENOSPC"}})
        self.assertIn("ENOSPC", self.cs.ensure_reminder_checker())
        self.cs.detach.assert_not_called()

    def test_user_prompt_hook_passes_repair_note_even_for_non_user_turns(self):
        self.cs.ensure_reminder_checker = lambda: "[claudesec watchdog] restarted only it"
        self.cs.hook_input = lambda: {"prompt": "[claudesec alert] ..."}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.cs.hook_secretary_prompt()
        payload = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertIn("restarted only it", payload["additionalContext"])
        self.assertFalse((self.home / self.cs.USER_TURN).exists())

    def test_detach_can_start_even_when_its_log_cannot_be_opened(self):
        original = open
        child = mock.Mock()

        def opening(path, *a, **kw):
            if Path(path) == self.home / "lifecycle.log":
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch("builtins.open", opening), \
                mock.patch.object(self.cs.subprocess, "Popen", return_value=child) as popen, \
                mock.patch.object(self.cs.threading, "Thread"):
            self.real_detach("reminders", run_pid=42)
        self.assertEqual(popen.call_args.args[0][-2:], ["reminders", "42"])
        self.assertEqual(popen.call_args.kwargs["stdout"], self.cs.subprocess.DEVNULL)


class Outbox(Base):
    def seed(self, *items):
        due = (dt.datetime.now().astimezone() - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
        data = [{"id": f"r{i+1}", "due": due, "status": "pending", "every": None,
                 "target": "secretary", "text": f"task {i+1}", **extra} for i, extra in enumerate(items or ({},))]
        (self.home / "reminders.json").write_text(json.dumps({"next_id": len(data) + 1, "items": data}))

    def saved(self):
        return json.loads((self.home / "reminders.json").read_text())

    def fire(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.fire_due_reminders()

    def test_claim_write_failure_leaves_reminder_pending(self):
        self.seed()
        original = Path.write_text

        def write(path, *a, **kw):
            if path.name == "reminders.tmp":
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "write_text", write), self.assertRaises(OSError):
            self.fire()
        self.assertEqual(self.saved()["items"][0]["status"], "pending")
        self.assertFalse(list((self.home / "alerts").glob("*.txt")))
        self.fire()
        self.assertEqual(len(list((self.home / "alerts").glob("*.txt"))), 1)
        self.assertEqual(self.saved()["outbox"], [])

    def test_alert_write_failure_is_retried_from_the_durable_outbox(self):
        self.seed()
        original = Path.write_text

        def write(path, *a, **kw):
            if path.parent == self.home / "alerts":
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "write_text", write), self.assertRaises(RuntimeError):
            self.fire()
        self.assertEqual(self.saved()["items"][0]["status"], "fired")
        self.assertEqual(len(self.saved()["outbox"]), 1)
        self.fire()
        self.assertEqual(len(list((self.home / "alerts").glob("*.txt"))), 1)
        self.assertEqual(self.saved()["outbox"], [])
        self.assertFalse(list((self.home / "alerts").glob(".*")))

    def test_result_commit_failure_cannot_duplicate_an_already_delivered_alert(self):
        self.seed()
        original = Path.write_text
        commits = 0

        def write(path, *a, **kw):
            nonlocal commits
            if path.name == "reminders.tmp":
                commits += 1
                if commits == 2:
                    raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "write_text", write), self.assertRaises(RuntimeError):
            self.fire()
        alerts = self.home / "alerts"
        (alerts / "delivered").mkdir()
        (alert,) = alerts.glob("*.txt")
        alert.rename(alerts / "delivered" / alert.name)
        self.fire()
        self.assertEqual(self.saved()["outbox"], [])
        self.assertEqual(list(alerts.glob("*.txt")), [])
        self.assertEqual(len(list((alerts / "delivered").glob("*.txt"))), 1)

    def test_one_failed_delivery_does_not_hold_up_the_others(self):
        self.seed({}, {})
        self.cs.deliver_reminder = lambda r: (_ for _ in ()).throw(full_disk()) if r["id"] == "r1" else "delivered"
        with self.assertRaises(RuntimeError):
            self.fire()
        self.assertEqual([r["id"] for r in self.saved()["outbox"]], ["r1"])
        self.assertEqual(self.saved()["items"][1]["result"], "delivered")

    def test_cancelled_and_future_reminders_are_not_delivered(self):
        future = (dt.datetime.now().astimezone() + dt.timedelta(days=1)).isoformat()
        self.seed({"status": "cancelled"}, {"due": future})
        self.cs.deliver_reminder = mock.Mock()
        self.fire()
        self.cs.deliver_reminder.assert_not_called()

    def test_repeating_occurrence_keeps_its_payload_for_retry_and_future_schedule(self):
        self.seed({"every": 7200})
        self.cs.deliver_reminder = mock.Mock(side_effect=full_disk())
        with self.assertRaises(RuntimeError):
            self.fire()
        saved = self.saved()
        self.assertGreater(dt.datetime.fromisoformat(saved["items"][0]["due"]), dt.datetime.now().astimezone())
        original = saved["outbox"][0]["due"]
        self.cs.deliver_reminder = mock.Mock(return_value="delivered")
        self.fire()
        self.assertEqual(self.cs.deliver_reminder.call_args.args[0]["due"], original)
        self.assertEqual(self.saved()["outbox"], [])

    def test_cancel_a_periodic_task_also_cancels_its_undelivered_occurrence(self):
        self.seed({"every": 7200})
        self.cs.deliver_reminder = mock.Mock(side_effect=full_disk())
        with self.assertRaises(RuntimeError):
            self.fire()
        with contextlib.redirect_stdout(io.StringIO()):
            self.cs.cmd_remind(argparse.Namespace(cancel="r1"))
        self.assertEqual(self.saved()["outbox"], [])
        self.assertEqual(self.saved()["items"][0]["status"], "cancelled")


class Watcher(Base):
    def setUp(self):
        super().setUp()
        self.cs.claude_sessions = lambda: []
        self.cs.hook_input = lambda: {"hook_event_name": "Stop", "session_id": "s"}
        self.alerts = self.home / "alerts"
        (self.alerts / "delivered").mkdir(parents=True)
        (self.alerts / "1.txt").write_text("first alert")
        (self.alerts / "2.txt").write_text("second alert")

    def run_hook(self):
        with mock.patch.object(self.cs.time, "sleep"), contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e:
            self.cs.hook_secretary_watch()
        self.assertEqual(e.exception.code, 2)
        return err.getvalue()

    def test_watch_pid_write_failure_retries_without_losing_alerts(self):
        original = Path.write_text
        failed = False

        def write(path, *a, **kw):
            nonlocal failed
            if path.name == "watch.pid" and not failed:
                failed = True
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "write_text", write):
            out = self.run_hook()
        self.assertIn("recovered", out)
        self.assertIn("first alert", out)
        self.assertIn("second alert", out)

    def test_inflight_write_failure_happens_before_any_alert_is_moved(self):
        original = Path.write_text
        failed = False

        def write(path, *a, **kw):
            nonlocal failed
            if path.name.startswith("inflight.") and path.suffix == ".tmp" and not failed:
                failed = True
                self.assertTrue((self.alerts / "1.txt").exists())
                self.assertTrue((self.alerts / "2.txt").exists())
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "write_text", write):
            out = self.run_hook()
        self.assertEqual(json.loads((self.alerts / "inflight.json").read_text()), ["1.txt", "2.txt"])
        self.assertIn("first alert", out)
        self.assertIn("second alert", out)

    def test_partial_rename_failure_retains_the_whole_batch_for_retry(self):
        original = Path.rename
        failed = False

        def rename(path, *a, **kw):
            nonlocal failed
            if path == self.alerts / "2.txt" and not failed:
                failed = True
                self.assertTrue((self.alerts / "delivered" / "1.txt").exists())
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "rename", rename):
            out = self.run_hook()
        self.assertEqual(out.count("first alert"), 1)
        self.assertEqual(out.count("second alert"), 1)
        self.assertEqual(list(self.alerts.glob("*.txt")), [])

    def test_retry_stops_when_its_owner_exits(self):
        with mock.patch.object(Path, "write_text", side_effect=full_disk()), \
                mock.patch.object(self.cs.os, "kill", side_effect=[None, OSError(errno.ESRCH, "gone")]), \
                mock.patch.object(self.cs.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.cs.hook_secretary_watch()
        self.assertTrue((self.alerts / "1.txt").exists())

    def test_failed_turn_requeue_failure_keeps_its_receipt_until_recovered(self):
        self.cs.hook_input = lambda: {"hook_event_name": "StopFailure", "session_id": "s"}
        for alert in self.alerts.glob("*.txt"):
            alert.rename(self.alerts / "delivered" / alert.name)
        (self.alerts / "inflight.json").write_text(json.dumps(["1.txt", "2.txt"]))
        original = Path.rename
        failed = False

        def rename(path, *a, **kw):
            nonlocal failed
            if path == self.alerts / "delivered" / "2.txt" and not failed:
                failed = True
                self.assertTrue((self.alerts / "inflight.json").exists())
                raise full_disk()
            return original(path, *a, **kw)

        with mock.patch.object(Path, "rename", rename):
            out = self.run_hook()
        self.assertEqual(out.count("first alert"), 1)
        self.assertEqual(out.count("second alert"), 1)
        self.assertIn("recovered", out)


class LiveProcesses(Base):
    """Exercise real process/lock lifetimes, but no tmux, model, worker, or host-wide scans."""

    def wait_for(self, fn):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if fn():
                return
            time.sleep(0.02)
        self.fail("isolated checker did not reach expected state")

    def test_full_disk_retry_and_dead_checker_repair_with_real_processes(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                                  str(self.cs.SELF), "run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children = []
        jobs = []
        runner = """
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader('isolated_checker', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
cs = importlib.util.module_from_spec(spec)
loader.exec_module(cs)
for name in ('check_watch', 'check_stuck', 'check_runaway', 'check_stale', 'check_leftovers', 'check_idle'):
    setattr(cs, name, lambda: None)
cs.REMINDER_CHECK_SECS = 0.05
original = cs.fire_due_reminders
failed = False
def fire():
    global failed
    if not failed:
        failed = True
        raise OSError(28, 'No space left on device (isolated fault injection)')
    original()
cs.fire_due_reminders = fire
cs.check_reminders(int(sys.argv[2]))
"""

        def detach(job, run_pid=None):
            jobs.append(job)
            self.assertEqual((job, run_pid), ("reminders", owner.pid))
            p = subprocess.Popen([sys.executable, "-c", runner, str(self.cs.SELF), str(run_pid)],
                                 env={**os.environ, "CLAUDESEC_HOME": str(self.home)},
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.append(p)
            return p

        self.cs.detach = detach
        try:
            (self.home / "run.pid").write_text(str(owner.pid))
            self.assertEqual(self.cs.current_run(), owner.pid)
            self.assertIn("restarted only it", self.cs.ensure_reminder_checker())
            self.wait_for(lambda: len(list((self.home / "alerts").glob("*.txt"))) >= 2)
            self.assertIsNone(children[0].poll())  # ENOSPC was retried, not a process exit
            self.wait_for(lambda: not json.loads((self.home / self.cs.CHECKER_STATE).read_text())["errors"])
            self.assertIsNone(self.cs.ensure_reminder_checker())
            children[0].terminate()  # only the isolated checker, not the owner or any real session
            children[0].wait(timeout=5)
            self.assertFalse(self.cs.reminder_checker_running())
            self.assertIn("restarted only it", self.cs.ensure_reminder_checker())
            self.assertEqual(jobs, ["reminders", "reminders"])
            self.assertIsNone(owner.poll())
            owner.terminate()
            owner.wait(timeout=5)
            children[1].wait(timeout=5)  # follows its owner and releases the lock
            self.assertFalse(self.cs.reminder_checker_running())
        finally:
            for p in [*children, owner]:
                if p.poll() is None:
                    p.terminate()
                p.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
