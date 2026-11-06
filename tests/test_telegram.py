"""plugins/telegram, linked into a temporary $CLAUDESEC_HOME the way a user enables it, sending to a
fake Bot API on 127.0.0.1 (never api.telegram.org): text, photos with a caption, files, long text in
pieces, a 429 waited out, a photo Telegram refuses sent as a file; no config, a config others can
read, a missing file: refused before anything is sent. The token shows up in no output.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

from test_external_sessions import load

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "telegram"
TOKEN = "123456789:AAH-secret_token_value_xyz0123456789"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class FakeBotAPI(ThreadingHTTPServer):
    """Records each call as (token, method, fields, {field: (filename, bytes)}); answers from
    self.answers[method] (a list, taken in order; the last one stays), else ok."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls, self.answers = [], {}

    def answer(self, method):
        got = self.answers.get(method)
        if not got:
            return 200, {"ok": True, "result": {"message_id": len(self.calls)}}
        return got.pop(0) if len(got) > 1 else got[0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        _, bot, method = self.path.split("/")
        body = self.rfile.read(int(self.headers["Content-Length"]))
        ctype = self.headers["Content-Type"]
        fields, files = {}, {}
        if ctype.startswith("multipart/"):
            msg = BytesParser(policy=email_policy).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    files[name] = (part.get_filename(), part.get_payload(decode=True))
                else:
                    fields[name] = part.get_content().strip()
        else:
            fields = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        self.server.calls.append((bot[3:], method, fields, files))
        code, reply = self.server.answer(method)
        data = json.dumps(reply).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Telegram(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "plugins").mkdir()
        (self.home / "plugins" / "telegram").symlink_to(PLUGIN_DIR)
        (self.home / "config.json").write_text(json.dumps({"language": "en", "cursor": False}))
        self.cs = load(self.home)
        self.server = FakeBotAPI()
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        self.env = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        self.env.start()
        self.files = self.home / "out"
        self.files.mkdir()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def configure(self, mode=0o600, **conf):
        conf = {"bot_token": TOKEN, "chat_id": -1001234567890,
                "api_base": f"http://127.0.0.1:{self.server.server_port}", **conf}
        path = self.home / "telegram.json"
        path.write_text(json.dumps(conf))
        path.chmod(mode)

    def run_cs(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["claudesec", *argv]), mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.cs.main()
            except SystemExit as e:
                code = e.code
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        self.assertNotIn(TOKEN.split(":")[1], out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def file(self, name, data=b"hello"):
        path = self.files / name
        path.write_bytes(data)
        return str(path)

    def methods(self):
        return [c[1] for c in self.server.calls]

    def test_text(self):
        self.configure()
        code, out, _ = self.run_cs("telegram", "send", "-t", "hello there")
        self.assertEqual(code, 0)
        self.assertEqual(self.server.calls, [(TOKEN, "sendMessage", {"chat_id": "-1001234567890", "text": "hello there"}, {})])
        self.assertIn("sent: text", out)

    def test_text_from_stdin(self):
        self.configure()
        self.run_cs("telegram", "send", "-t", "-", stdin="it's \"quoted\"\nsecond line\n")
        self.assertEqual(self.server.calls[0][2]["text"], "it's \"quoted\"\nsecond line")

    def test_long_text_in_pieces(self):
        self.configure()
        text = "\n".join(f"line {i} " + "x" * 90 for i in range(100))  # ~10000 characters
        self.run_cs("telegram", "send", "-t", text)
        sent = [c[2]["text"] for c in self.server.calls]
        self.assertEqual(self.methods(), ["sendMessage"] * 3)
        self.assertTrue(all(len(s) <= 4096 for s in sent))
        self.assertEqual("\n".join(sent), text)

    def test_one_long_line_cut(self):
        self.assertEqual(self.ns["chunks"]("ab" * 5, 4), ["abab", "abab", "ab"])
        self.assertEqual(self.ns["chunks"]("😀😀😀", 4), ["😀😀", "😀"])  # two UTF-16 units each

    @property
    def ns(self):
        return next(p for p in self.cs.PLUGINS if p["__name__"] == "claudesec_plugin_telegram")

    def test_photo_with_caption(self):
        self.configure()
        shot = self.file("shot.png", PNG)
        code, out, _ = self.run_cs("telegram", "send", "-t", "the screenshot", shot)
        self.assertEqual(code, 0)
        [(_, method, fields, files)] = self.server.calls
        self.assertEqual(method, "sendPhoto")
        self.assertEqual(fields, {"chat_id": "-1001234567890", "caption": "the screenshot"})
        self.assertEqual(files, {"photo": ("shot.png", PNG)})
        self.assertIn("sent: photo shot.png, with the text", out)

    def test_document(self):
        self.configure()
        self.run_cs("telegram", "send", self.file("report.pdf", b"%PDF-1.4"), self.file("qr.png", PNG), "--document")
        self.assertEqual(self.methods(), ["sendDocument", "sendDocument"])
        self.assertEqual(self.server.calls[0][3], {"document": ("report.pdf", b"%PDF-1.4")})
        self.assertEqual(self.server.calls[1][3], {"document": ("qr.png", PNG)})

    def test_text_and_two_files(self):
        self.configure()
        self.run_cs("telegram", "send", "-t", "both", self.file("a.png", PNG), self.file("b.txt"))
        self.assertEqual(self.methods(), ["sendMessage", "sendPhoto", "sendDocument"])
        self.assertNotIn("caption", self.server.calls[1][2])

    def test_refused_photo_goes_as_file(self):
        self.configure()
        self.server.answers["sendPhoto"] = [(400, {"ok": False, "error_code": 400, "description": "Bad Request: PHOTO_INVALID_DIMENSIONS"})]
        code, out, _ = self.run_cs("telegram", "send", self.file("tall.png", PNG))
        self.assertEqual(code, 0)
        self.assertEqual(self.methods(), ["sendPhoto", "sendDocument"])
        self.assertIn("sent: file tall.png", out)

    def test_429_waited_out(self):
        self.configure()
        self.server.answers["sendMessage"] = [
            (429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 3", "parameters": {"retry_after": 3}}),
            (200, {"ok": True, "result": {}})]
        with mock.patch("time.sleep") as slept:
            code, _, _ = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 0)
        slept.assert_called_once_with(3)
        self.assertEqual(self.methods(), ["sendMessage", "sendMessage"])

    def test_api_error(self):
        self.configure()
        self.server.answers["sendMessage"] = [(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
        code, out, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("sendMessage: Unauthorized", err)

    def test_group_upgraded(self):
        self.configure()
        self.server.answers["sendMessage"] = [(400, {"ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup chat",
                                                     "parameters": {"migrate_to_chat_id": -1009876}})]
        code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("-1009876", err)

    def test_unreachable(self):
        self.configure(api_base="http://127.0.0.1:1")
        code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("sendMessage:", err)

    def test_not_configured(self):
        code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("@BotFather", err)
        self.assertIn(str(self.home / "telegram.json"), err)
        self.assertEqual(self.server.calls, [])

    def test_readable_by_others(self):
        self.configure(mode=0o644)
        code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("chmod 600", err)
        self.assertEqual(self.server.calls, [])

    def test_bad_token(self):
        self.configure(bot_token="not a token")
        code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("bot_token", err)

    def test_missing_file_sends_nothing(self):
        self.configure()
        code, _, err = self.run_cs("telegram", "send", "-t", "hi", self.file("a.png", PNG), str(self.files / "gone.pdf"))
        self.assertEqual(code, 1)
        self.assertIn("gone.pdf", err)
        self.assertEqual(self.server.calls, [])

    def test_nothing_to_send(self):
        self.configure()
        code, _, err = self.run_cs("telegram", "send")
        self.assertEqual(code, 1)
        self.assertEqual(self.server.calls, [])

    def test_check(self):
        self.configure()
        self.server.answers["getMe"] = [(200, {"ok": True, "result": {"username": "my_bot"}})]
        self.server.answers["getChat"] = [(200, {"ok": True, "result": {"id": -1001234567890, "type": "supergroup", "title": "Inbox"}})]
        code, out, _ = self.run_cs("telegram", "check")
        self.assertEqual(code, 0)
        self.assertIn("@my_bot", out)
        self.assertIn("Inbox", out)

    def test_chats_without_chat_id(self):
        self.configure(chat_id=None)
        self.server.answers["getUpdates"] = [(200, {"ok": True, "result": [
            {"update_id": 1, "my_chat_member": {"chat": {"id": -1001, "type": "supergroup", "title": "Inbox"}}},
            {"update_id": 2, "message": {"chat": {"id": 42, "type": "private", "first_name": "Me"}, "text": "/start"}}]})]
        code, out, _ = self.run_cs("telegram", "chats")
        self.assertEqual(code, 0)
        self.assertIn("-1001\tsupergroup\tInbox", out)
        self.assertIn("42\tprivate\tMe", out)
        self.assertNotIn("offset", self.server.calls[0][2])  # nothing marked as seen

    def test_proxy(self):
        self.configure(proxy="http://127.0.0.1:1")
        with mock.patch.dict(os.environ, {"no_proxy": "", "NO_PROXY": ""}):
            code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)  # went to the (dead) proxy, not to the server
        self.assertEqual(self.server.calls, [])

    def test_prompt_for_the_secretary_only(self):
        self.assertIn("claudesec telegram send", self.cs.plugin_prompt({}, self.cs.SECRETARY_WHO))
        worker = {"role": "worker", "name": "w", "agent": "claude", "discuss": False}
        self.assertEqual(self.cs.plugin_prompt({}, worker), "")

    def test_unexpected_error_scrubbed(self):
        self.configure()
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=RuntimeError(f"boom /bot{TOKEN}/sendMessage")):
            code, _, err = self.run_cs("telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("boom /bot<token>/sendMessage", err)


if __name__ == "__main__":
    unittest.main()
