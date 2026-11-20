"""bin/claude-ark-deepseek: the Anthropic Messages <-> ARK Chat Completions translation Claude
Code sees, and the request/stream shapes it answers with. Nothing here talks to ARK: the upstream
call is replaced by a stub, so the tests need no key and no network.

    python3 -m unittest discover -s tests
"""
import http.client
import importlib.machinery
import importlib.util
import json
import threading
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "claude-ark-deepseek"


def load():
    loader = importlib.machinery.SourceFileLoader("claude_ark_deepseek", str(BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeResponse:
    """What urlopen returns: SSE lines, or one JSON body."""

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.lines)

    def read(self):
        return b"".join(self.lines)

    def close(self):
        pass


def chunks(*events):
    return [b"data: " + json.dumps(e).encode() + b"\n" for e in events] + [b"data: [DONE]\n"]


class Translate(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_system_messages_and_sampling(self):
        out = self.m.translate_request({
            "model": "claude-opus-5-5", "max_tokens": 64, "temperature": 0.3,
            "system": [{"type": "text", "text": "Be terse.", "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }, "ep-1")
        self.assertEqual(out["model"], "ep-1")
        self.assertEqual(out["messages"], [{"role": "system", "content": "Be terse."},
                                           {"role": "user", "content": "hi"}])
        self.assertEqual((out["max_tokens"], out["temperature"]), (64, 0.3))
        self.assertNotIn("tools", out)

    def test_tool_use_and_tool_results(self):
        out = self.m.translate_request({
            "model": "x", "max_tokens": 8,
            "messages": [
                {"role": "user", "content": "weather?"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "tu_1", "name": "get_weather", "input": {"city": "Beijing"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": [{"type": "text", "text": "sunny"}]},
                    {"type": "text", "text": "and now?"}]},
            ],
            "tools": [{"name": "get_weather", "description": "w", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "any"},
        }, "ep-1")
        assistant = out["messages"][1]
        self.assertEqual(assistant["content"], "checking")  # thinking dropped
        self.assertEqual(assistant["tool_calls"], [{"id": "tu_1", "type": "function", "function": {
            "name": "get_weather", "arguments": '{"city": "Beijing"}'}}])
        # The tool result comes before the user's own words, and carries the call's id.
        self.assertEqual(out["messages"][2], {"role": "tool", "tool_call_id": "tu_1", "content": "sunny"})
        self.assertEqual(out["messages"][3], {"role": "user", "content": "and now?"})
        self.assertEqual(out["tools"], [{"type": "function", "function": {
            "name": "get_weather", "description": "w", "parameters": {"type": "object"}}}])
        self.assertEqual(out["tool_choice"], "required")

    def test_tool_choice_tool_and_stop_reasons(self):
        m = self.m
        self.assertEqual(m.tool_choice({"type": "tool", "name": "Bash"}),
                         {"type": "function", "function": {"name": "Bash"}})
        self.assertEqual([m.stop_reason(f) for f in ("stop", "length", "tool_calls", "junk")],
                         ["end_turn", "max_tokens", "tool_use", "end_turn"])
        self.assertEqual(m.usage_of({"prompt_tokens": 100, "completion_tokens": 5,
                                     "prompt_tokens_details": {"cached_tokens": 60}}),
                         {"input_tokens": 40, "output_tokens": 5,
                          "cache_read_input_tokens": 60, "cache_creation_input_tokens": 0})


class Proxy(unittest.TestCase):
    """The whole HTTP side: claude's request in, Anthropic SSE out."""

    def setUp(self):
        self.m = load()
        self.upstream = []

    def start(self, lines):
        m = self.m
        m.urllib.request.urlopen = lambda req, timeout=None: (self.upstream.append(req), FakeResponse(lines))[1]
        self.srv = m.Server(("127.0.0.1", 0), m.Handler)
        self.srv.cfg = {"api_key": "k", "model": "ep-1", "base_url": "https://example.test/api/v3",
                        "debug": False, "context": 1000, "timeout": 5}
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        r = conn.getresponse()
        text = r.read().decode()
        conn.close()
        return r.status, text

    def events(self, text):
        out = []
        for line in text.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
        return out

    def test_stream_text_then_tool_call(self):
        self.start(chunks(
            {"choices": [{"delta": {"content": "I'll "}}]},
            {"choices": [{"delta": {"content": "look."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                                                    "function": {"name": "get_weather", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"city"'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': "Beijing"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
             "usage": {"prompt_tokens": 30, "completion_tokens": 9,
                       "prompt_tokens_details": {"cached_tokens": 10}}},
        ))
        status, body = self.post("/v1/messages?beta=true", {
            "model": "claude-opus-5-5", "max_tokens": 32, "stream": True,
            "messages": [{"role": "user", "content": "weather?"}]})
        self.assertEqual(status, 200)
        sent = json.loads(self.upstream[0].data)
        self.assertTrue(sent["stream"])
        self.assertEqual(self.upstream[0].full_url, "https://example.test/api/v3/chat/completions")
        ev = self.events(body)
        self.assertEqual(ev[0]["type"], "message_start")
        starts = [e["content_block"] for e in ev if e["type"] == "content_block_start"]
        self.assertEqual(starts[0]["type"], "text")
        self.assertEqual(starts[1]["type"], "tool_use")
        self.assertEqual((starts[1]["id"], starts[1]["name"]), ("call_1", "get_weather"))
        deltas = [e["delta"] for e in ev if e["type"] == "content_block_delta"]
        self.assertEqual("".join(d["text"] for d in deltas if d["type"] == "text_delta"), "I'll look.")
        self.assertEqual("".join(d["partial_json"] for d in deltas if d["type"] == "input_json_delta"),
                         '{"city": "Beijing"}')
        last = ev[-2]
        self.assertEqual(last["type"], "message_delta")
        self.assertEqual(last["delta"]["stop_reason"], "tool_use")
        self.assertEqual(last["usage"], {"input_tokens": 20, "output_tokens": 9,
                                         "cache_read_input_tokens": 10, "cache_creation_input_tokens": 0})
        self.assertEqual(ev[-1]["type"], "message_stop")
        # Every block that opened is closed, in order.
        opened = [e["index"] for e in ev if e["type"] == "content_block_start"]
        closed = [e["index"] for e in ev if e["type"] == "content_block_stop"]
        self.assertEqual(opened, closed)

    def test_whole_answer_and_count_tokens(self):
        self.start([json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"},
                                             "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 7, "completion_tokens": 2}}).encode()])
        status, body = self.post("/v1/messages", {"model": "x", "max_tokens": 8,
                                                  "messages": [{"role": "user", "content": "hi"}]})
        msg = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(msg["content"], [{"type": "text", "text": "OK"}])
        self.assertEqual(msg["stop_reason"], "end_turn")
        self.assertEqual(msg["usage"]["input_tokens"], 7)
        status, body = self.post("/v1/messages/count_tokens", {"messages": [{"role": "user", "content": "x" * 400}]})
        self.assertEqual(status, 200)
        self.assertGreater(json.loads(body)["input_tokens"], 90)


if __name__ == "__main__":
    unittest.main()
