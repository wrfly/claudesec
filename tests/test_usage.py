"""Token usage per worker, from its session transcript (and its subagents'), counted once per
response however many lines it was streamed in, and read on from where the cache left off.

    python3 -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_external_sessions import load


def reply(mid, inp, write, read, out):
    return {"type": "assistant", "message": {"id": mid, "usage": {
        "input_tokens": inp, "cache_creation_input_tokens": write, "cache_read_input_tokens": read,
        "output_tokens": out}}}


class Usage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cs = load(self.root / "state")
        (self.root / "state").mkdir()
        self.saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root / "config")
        self.project = self.root / "config" / "projects" / "-proj"
        self.project.mkdir(parents=True)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved
        self.tmp.cleanup()

    def write(self, path, *entries, partial=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write("".join(json.dumps(e) + "\n" for e in entries) + partial)

    def test_counts_each_response_once_across_reads(self):
        t = self.project / "sid.jsonl"
        self.write(t, {"type": "user", "message": {"content": "hi"}},
                   reply("m1", 3, 100, 0, 1), reply("m1", 3, 100, 0, 20),  # one response, two blocks
                   reply("m2", 1, 10, 100, 5))
        self.assertEqual(self.cs.session_usage(["sid"]), {"sid": [4, 110, 100, 25]})
        # appended later: the last response gets another block, a new one starts, a line is half-written
        self.write(t, reply("m2", 1, 10, 100, 9), reply("m3", 2, 0, 500, 7), partial='{"type": "assist')
        self.assertEqual(self.cs.session_usage(["sid"]), {"sid": [6, 110, 600, 36]})
        cache = json.loads((self.root / "state" / "usage.json").read_text())
        self.assertEqual(cache[str(t)]["offset"], t.stat().st_size - len('{"type": "assist'))

    def test_subagents_count_and_missing_sessions_dont(self):
        self.write(self.project / "sid.jsonl", reply("m1", 1, 0, 0, 1))
        self.write(self.project / "sid" / "subagents" / "agent-a.jsonl", reply("s1", 10, 0, 0, 10))
        self.assertEqual(self.cs.session_usage(["sid", "gone"]), {"sid": [11, 0, 0, 11], "gone": None})

    def test_a_rewritten_transcript_is_read_again(self):
        t = self.project / "sid.jsonl"
        self.write(t, reply("m1", 1, 0, 0, 1), reply("m2", 1, 0, 0, 1))
        self.cs.session_usage(["sid"])
        t.write_text(json.dumps(reply("m9", 5, 0, 0, 5)) + "\n")
        self.assertEqual(self.cs.session_usage(["sid"]), {"sid": [5, 0, 0, 5]})

    def test_worker_usage_and_its_words(self):
        self.write(self.project / "sid.jsonl", reply("m1", 1000, 200000, 3000000, 54000))
        workers = {"a": {"session": "sid"}, "c": {"session": "x", "agent": "cursor"}, "n": {"session": None}}
        use = self.cs.worker_usage(workers)
        self.assertEqual(use, {"a": [1000, 200000, 3000000, 54000], "c": None, "n": None})
        self.assertEqual(self.cs.usage_word(use["a"]), "3.3M")
        self.assertEqual(self.cs.usage_word(None), "-")
        self.assertEqual([self.cs.short_count(n) for n in (999, 1234, 56789, 2_500_000_000)],
                         ["999", "1.2K", "57K", "2.5B"])
        self.assertIn("缓存读 3.0M", self.cs.usage_detail(use["a"]))


if __name__ == "__main__":
    unittest.main()
