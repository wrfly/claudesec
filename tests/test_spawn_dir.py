"""`claudesec spawn` without --dir: the worker gets ~/.claudesec/work/<name>, made if missing.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class Stop(Exception):
    pass


class SpawnDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "state"
        self.home.mkdir()
        self.cs = load(self.home)
        (self.home / "registry.json").write_text(json.dumps({"focus": None, "workers": {}}))
        self.dirs = []

        def prepare(name, cfg, initial, cwd, **kw):
            self.dirs.append(cwd)
            raise Stop()  # stopped right before a window would open
        self.cs.prepare_worker = prepare

    def tearDown(self):
        self.tmp.cleanup()

    def spawn(self, *argv):
        saved_argv, saved_stdin = sys.argv, sys.stdin
        sys.argv, sys.stdin = ["claudesec", "spawn", *argv], io.StringIO("do it")
        try:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Stop):
                self.cs.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin

    def test_no_dir_means_its_own_work_dir(self):
        self.spawn("vpsproxy")
        work = (self.home / "work" / "vpsproxy").resolve()
        self.assertEqual(self.dirs, [str(work)])
        self.assertTrue(work.is_dir())
        self.spawn("vpsproxy")  # already there: used as it is
        self.assertEqual(self.dirs[-1], str(work))

    def test_dir_given_is_used(self):
        self.spawn("proj", "--dir", self.tmp.name)
        self.assertEqual(self.dirs, [str(Path(self.tmp.name).resolve())])
        self.assertFalse((self.home / "work").exists())


if __name__ == "__main__":
    unittest.main()
