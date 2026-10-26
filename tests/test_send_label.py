"""What claudesec pastes into a Cursor worker's window says who it comes from, so the worker
doesn't take it for the user typing (「用户直接跟我说了」).

    python3 -m unittest discover -s tests
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


class SendLabel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cs = load(Path(self.tmp.name))
        (Path(self.tmp.name) / "config.json").write_text(json.dumps({"language": "zh"}))
        self.pasted = []
        # subprocess and time are the shared stdlib modules: put them back afterwards
        self.saved = self.cs.subprocess.run, self.cs.time.sleep
        self.cs.subprocess.run = lambda cmd, input=None, **kw: self.pasted.append(input)
        self.cs.time.sleep = lambda s: None
        self.cs.tmux = lambda *a, **k: ""

    def tearDown(self):
        self.cs.subprocess.run, self.cs.time.sleep = self.saved
        self.tmp.cleanup()

    def test_secretary_and_delegator_messages_are_labelled(self):
        self.cs.send_text("w", {"window": "@1"}, "用户决策：A")
        self.cs.send_text("w", {"window": "@1"}, "再改一处", by="plan-scale")
        self.assertTrue(self.pasted[0].startswith("[claudesec] 秘书转来的消息"), self.pasted[0])
        self.assertTrue(self.pasted[0].endswith("\n用户决策：A"))
        self.assertIn("委托你的 worker plan-scale", self.pasted[1])
        self.assertIn("不是用户在你窗口里打的字", self.pasted[1])
        log = self.cs.read_log("w")
        self.assertEqual([b.strip() for _, b in log], ["用户决策：A", "再改一处"])  # the log keeps the words as sent


if __name__ == "__main__":
    unittest.main()
