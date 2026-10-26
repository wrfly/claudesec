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


def billed(mid, ts, model="claude-opus-5-5", speed="standard", inp=0, w5=0, w1=0, read=0, out=0):
    return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "usage": {
        "input_tokens": inp, "cache_creation_input_tokens": w5 + w1, "cache_read_input_tokens": read,
        "output_tokens": out, "speed": speed,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}}}


class UsageBuckets(Usage):
    """By hour, model and speed, cache writes split by TTL, for every session claudesec ran."""

    def hour(self, iso):
        import datetime as dt
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%dT%H")

    def test_buckets_split_ttl_model_speed_and_hour(self):
        t = self.project / "sid.jsonl"
        self.write(t, billed("m1", "2026-10-26T01:10:00Z", w1=100, out=1),
                   billed("m1", "2026-10-26T01:10:01Z", w1=100, out=9),  # same response, later block
                   billed("m2", "2026-10-26T02:30:00Z", model="claude-haiku-4-5-20251001", w5=50, read=7),
                   billed("m3", "2026-10-26T02:40:00Z", speed="fast", inp=3, out=4))
        b = self.cs.session_buckets(["sid"])["sid"]
        h1, h2 = self.hour("2026-10-26T01:10:00Z"), self.hour("2026-10-26T02:30:00Z")
        self.assertEqual(b, {f"{h1}|claude-opus-5-5|standard": [0, 0, 100, 0, 9],
                             f"{h2}|claude-haiku-4-5-20251001|standard": [0, 50, 0, 7, 0],
                             f"{h2}|claude-opus-5-5|fast": [3, 0, 0, 0, 4]})
        # the 4-number total sums both writes
        self.assertEqual(self.cs.session_usage(["sid"]), {"sid": [3, 150, 7, 13]})

    def test_a_transcript_without_the_ttl_split_counts_writes_as_5_minute(self):
        e = reply("m1", 1, 40, 0, 1)
        e["timestamp"], e["message"]["model"] = "2026-10-26T01:00:00Z", "claude-opus-5-5"
        self.write(self.project / "sid.jsonl", e)
        (v,) = self.cs.session_buckets(["sid"])["sid"].values()
        self.assertEqual(v, [1, 40, 0, 0, 1])

    def test_every_session_claudesec_ran_is_counted_by_owner_and_period(self):
        import datetime as dt
        home = self.root / "state"
        self.write(self.project / "s-live.jsonl", billed("a", "2026-10-26T01:00:00Z", out=10))
        self.write(self.project / "s-live" / "subagents" / "agent-x.jsonl", billed("b", "2026-10-26T01:05:00Z", out=5))
        self.write(self.project / "s-gone.jsonl", billed("c", "2026-10-25T01:00:00Z", out=7))
        self.write(self.project / "s-sec.jsonl", billed("d", "2026-10-26T01:00:00Z", out=1))
        (home / "registry.json").write_text(json.dumps({"focus": None, "workers": {
            "live": {"status": "PROGRESS", "session": "s-live", "dir": "/tmp", "task": "t"}}}))
        (home / "history" / "2026-10").mkdir(parents=True)
        (home / "history" / "2026-10" / "2026-10-25-gone.md").write_text(self.cs.frontmatter(
            worker="gone", session="s-gone", tags=["x"]) + "body\n")
        (home / "secretary.session").write_text("s-sec\n")
        owners = self.cs.managed_sessions()
        self.assertEqual(owners, {"s-gone": "gone", "s-live": "live", "s-sec": "secretary"})
        every = self.cs.usage_by_owner()
        self.assertEqual(every["live"]["claude-opus-5-5|standard"][4], 15)  # its subagent counts with it
        self.assertEqual(every["gone"]["claude-opus-5-5|standard"][4], 7)
        since = dt.datetime.fromisoformat("2026-10-26T00:00:00+00:00").astimezone()
        self.assertNotIn("gone", self.cs.usage_by_owner(since=since))
