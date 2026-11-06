"""plugins/telegram, linked into a temporary $CLAUDESEC_HOME the way a user enables it, sending to a
fake Bot API on 127.0.0.1 (never api.telegram.org): text, photos with a caption, files, long text in
pieces or as message.txt, a 429 waited out (a long one not), a photo Telegram refuses sent as a file;
no config, a config others can read, a missing file, a credential (the config, ~/.ssh, a private key,
the token in a file), a mode-600 file outside allow_private and workers' output: refused before anything
is sent; the rest of ~/.claudesec, workers' mode-600 output, and allow_private's private files, sent.
The token shows up in no output.

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
        self.user_home = Path(self.tmp.name) / "user"   # $HOME, for the dot-directory rule
        self.user_home.mkdir()
        self.env = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*", "HOME": str(self.user_home)})
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
        path.chmod(0o644)
        return str(path)

    def methods(self):
        return [c[1] for c in self.server.calls]

    def test_text(self):
        self.configure()
        code, out, _ = self.run_cs("plugin", "telegram", "send", "-t", "hello there")
        self.assertEqual(code, 0)
        self.assertEqual(self.server.calls, [(TOKEN, "sendMessage", {"chat_id": "-1001234567890", "text": "hello there"}, {})])
        self.assertIn("sent: text", out)

    def test_text_from_stdin(self):
        self.configure()
        self.run_cs("plugin", "telegram", "send", "-t", "-", stdin="it's \"quoted\"\nsecond line\n")
        self.assertEqual(self.server.calls[0][2]["text"], "it's \"quoted\"\nsecond line")

    def test_long_text_in_pieces(self):
        self.configure()
        text = "\n".join(f"line {i} " + "x" * 90 for i in range(100))  # ~10000 characters
        self.run_cs("plugin", "telegram", "send", "-t", text)
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
        code, out, _ = self.run_cs("plugin", "telegram", "send", "-t", "the screenshot", shot)
        self.assertEqual(code, 0)
        [(_, method, fields, files)] = self.server.calls
        self.assertEqual(method, "sendPhoto")
        self.assertEqual(fields, {"chat_id": "-1001234567890", "caption": "the screenshot"})
        self.assertEqual(files, {"photo": ("shot.png", PNG)})
        self.assertIn("sent: photo shot.png, with the text", out)

    def test_document(self):
        self.configure()
        self.run_cs("plugin", "telegram", "send", self.file("report.pdf", b"%PDF-1.4"), self.file("qr.png", PNG), "--document")
        self.assertEqual(self.methods(), ["sendDocument", "sendDocument"])
        self.assertEqual(self.server.calls[0][3], {"document": ("report.pdf", b"%PDF-1.4")})
        self.assertEqual(self.server.calls[1][3], {"document": ("qr.png", PNG)})

    def test_text_and_two_files(self):
        self.configure()
        self.run_cs("plugin", "telegram", "send", "-t", "both", self.file("a.png", PNG), self.file("b.txt"))
        self.assertEqual(self.methods(), ["sendMessage", "sendPhoto", "sendDocument"])
        self.assertNotIn("caption", self.server.calls[1][2])

    def test_refused_photo_goes_as_file(self):
        self.configure()
        self.server.answers["sendPhoto"] = [(400, {"ok": False, "error_code": 400, "description": "Bad Request: PHOTO_INVALID_DIMENSIONS"})]
        code, out, _ = self.run_cs("plugin", "telegram", "send", self.file("tall.png", PNG))
        self.assertEqual(code, 0)
        self.assertEqual(self.methods(), ["sendPhoto", "sendDocument"])
        self.assertIn("sent: file tall.png", out)

    def test_text_too_long_goes_as_a_file(self):
        self.configure()
        text = "\n".join(f"line {i} " + "x" * 90 for i in range(200))  # ~20000 characters: 5 messages
        code, out, _ = self.run_cs("plugin", "telegram", "send", "-t", text)
        self.assertEqual(code, 0)
        self.assertEqual(self.methods(), ["sendDocument"])
        self.assertEqual(self.server.calls[0][3], {"document": ("message.txt", text.encode())})
        self.assertIn("message.txt", out)

    def test_each_message_reported_as_it_goes(self):
        self.configure()
        self.server.answers["sendMessage"] = [(200, {"ok": True, "result": {}}),
                                              (400, {"ok": False, "error_code": 400, "description": "Bad Request: nope"})]
        code, out, err = self.run_cs("plugin", "telegram", "send", "-t", "\n".join("y" * 90 for _ in range(60)))
        self.assertEqual(code, 1)
        self.assertIn("sent: text 1/2", out)
        self.assertIn("nope", err)

    def test_other_photo_errors_not_retried_as_file(self):
        self.configure()
        self.server.answers["sendPhoto"] = [(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})]
        code, _, err = self.run_cs("plugin", "telegram", "send", self.file("a.png", PNG))
        self.assertEqual(code, 1)
        self.assertEqual(self.methods(), ["sendPhoto"])
        self.assertIn("sendPhoto: Bad Request: chat not found", err)

    def test_long_429_not_waited(self):
        self.configure()
        self.server.answers["sendMessage"] = [
            (429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 40}}),
            (429, {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 30}})]
        with mock.patch("time.sleep") as slept:
            code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        slept.assert_called_once_with(40)  # 40 + 30 is over the minute
        self.assertIn("30s", err)

    def put(self, rel, data=b"hello", mode=0o644, top=None):
        path = (top or self.user_home) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def refused(self, path, why):
        code, _, err = self.run_cs("plugin", "telegram", "send", self.file("fine.png", PNG), str(path))
        self.assertEqual(code, 1, path)
        self.assertIn(why, err)
        self.assertIn("the user sends it from Telegram", err)
        self.assertEqual(self.server.calls, [])

    def test_credentials_never_sent(self):
        self.configure(allow_private=[str(self.user_home / "qr"), str(self.user_home / ".ssh")])
        self.refused(self.put(".ssh/id_rsa.pub", b"ssh-rsa AAAA"), "~/.ssh")
        self.refused(self.put(".config/gh/hosts.yml"), "~/.config/gh")
        self.refused(self.put(".docker/config.json"), "~/.docker")
        self.refused(self.put("work/.env"), "a file named .env")
        self.refused(self.put("qr/id_ed25519", b"x", 0o600), "a file named id_ed25519")
        self.refused(self.put("qr/innocent.txt", b"junk\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl", 0o600), "private key")
        self.refused(self.put("qr/copy.json", json.dumps({"t": TOKEN}).encode(), 0o600), "this bot's token")
        link = self.user_home / "qr" / "link.png"
        link.symlink_to(self.put(".aws/credentials"))
        self.refused(link, "~/.aws")

    def test_config_refused_under_any_name(self):
        self.configure()
        (self.files / "innocent.txt").symlink_to(self.home / "telegram.json")
        os.link(self.home / "telegram.json", self.files / "hard.txt")
        for path in (self.home / "telegram.json", self.files / "innocent.txt", self.files / "hard.txt"):
            self.refused(path, "Telegram config")

    def test_private_files_only_from_allow_private(self):
        self.configure(allow_private=["~/.claudesec/secrets/vpn-qr"])
        secret = self.put(".claudesec/secrets/admin.txt", b"hunter2", 0o600)
        self.refused(secret, "only its owner may read it")
        self.refused(self.put("Downloads/x.pdf", b"%PDF", 0o600), "allow_private")
        sneaky = self.user_home / ".claudesec/secrets/vpn-qr/also.txt"
        sneaky.parent.mkdir(parents=True)
        sneaky.symlink_to(secret)  # in the allowed directory, but the file it is isn't
        self.refused(sneaky, "only its owner may read it")
        qr = self.put(".claudesec/secrets/vpn-qr/phone.png", PNG, 0o600)
        shot = self.put(".claudesec/reports/w/shot.png", PNG)  # ~/.claudesec isn't off limits
        code, out, _ = self.run_cs("plugin", "telegram", "send", str(qr), str(shot))
        self.assertEqual(code, 0)
        self.assertEqual(self.methods(), ["sendPhoto", "sendPhoto"])

    def test_workers_output_sent_though_600(self):
        """Workers write with umask 077: in $CLAUDESEC_HOME/work and scratch mode 600 isn't a sign of a secret.
        Elsewhere in $CLAUDESEC_HOME it still is, and credentials are refused there too."""
        self.configure(allow_private=["~/.claudesec/secrets/vpn-qr"])
        shot = self.put("work/prorate/screenshots/a.png", PNG, 0o600, top=self.home)
        report = self.put("scratch/pr1/report.md", b"# done\n", 0o600, top=self.home)
        code, _, err = self.run_cs("plugin", "telegram", "send", str(shot), str(report))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.methods(), ["sendPhoto", "sendDocument"])
        self.server.calls.clear()
        self.refused(self.put("secrets/admin.txt", b"hunter2", 0o600, top=self.home), "only its owner may read it")
        self.refused(self.put("backups/vpn/ss.txt", b"ss://x", 0o600, top=self.home), "only its owner may read it")
        self.refused(self.put("queue.md", b"x", 0o600, top=self.home), "only its owner may read it")
        self.refused(self.put("work/w/admin.token", b"abc", 0o600, top=self.home), "a file named admin.token")
        self.refused(self.put("work/w/relay.server.env", b"PASSWORD=x", 0o600, top=self.home), "a file named relay.server.env")
        self.refused(self.put("work/w/wg.conf", b"[Interface]\nPrivateKey = " + b"A" * 43 + b"=\n", 0o600, top=self.home),
                     "private key")
        self.refused(self.put("work/w/k.txt", b"-----BEGIN PRIVATE KEY-----", 0o600, top=self.home), "private key")
        secret = self.put("secrets/vpn.txt", b"hunter2", 0o600, top=self.home)
        os.link(secret, self.home / "work" / "w" / "vpn.txt")  # a secret's other name in work/
        self.refused(self.home / "work" / "w" / "vpn.txt", "2 hard links")
        (self.home / "work" / "w" / "s.txt").symlink_to(secret)  # where the file really is counts
        self.refused(self.home / "work" / "w" / "s.txt", "only its owner may read it")
        (self.home / "work" / "w" / "conf.json").symlink_to(self.home / "telegram.json")
        self.refused(self.home / "work" / "w" / "conf.json", "Telegram config")
        self.refused(self.put("work-not/x.png", PNG, 0o600, top=self.home), "only its owner may read it")

    def test_secret_text_not_sent(self):
        self.configure()
        key = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
        for argv, stdin in ((["-t", "-"], key), (["-t", "-"], key + "x\n" * 20000), (["-t", f"token {TOKEN}"], ""),
                            (["-t", "-", self.file("a.png", PNG)], "the caption " + key)):
            code, _, err = self.run_cs("plugin", "telegram", "send", *argv, stdin=stdin)
            self.assertEqual(code, 1, argv)
            self.assertIn("the text doesn't go out this way", err)
        self.assertEqual(self.server.calls, [])

    def test_what_is_checked_is_what_is_sent(self):
        self.configure()
        good = self.file("good.png", PNG)
        key = self.put(".ssh/id_ed25519", b"-----BEGIN OPENSSH PRIVATE KEY-----", 0o600)
        link = self.files / "shot.png"
        link.symlink_to(good)
        checked = self.ns["refused"]

        def swap_after(*a):  # the link turns to the key between the check and the send
            why = checked(*a)
            link.unlink()
            link.symlink_to(key)
            return why
        self.ns["refused"] = swap_after
        code, _, _ = self.run_cs("plugin", "telegram", "send", str(link))
        self.assertEqual(code, 0)
        self.assertEqual(self.server.calls[0][3]["photo"], ("shot.png", PNG))

    def test_allow_private_must_be_a_list_of_absolute_dirs(self):
        for bad, said in (("~/qr", "must be a list"), ([1], "must be a list"), (["qr"], "isn't an absolute path")):
            self.configure(allow_private=bad)
            code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
            self.assertEqual(code, 1)
            self.assertIn(said, err)
        self.assertEqual(self.server.calls, [])

    def test_private_hard_link_not_sent(self):
        self.configure(allow_private=["~/qr"])
        secret = self.put("notes/admin.txt", b"hunter2", 0o600)
        (self.user_home / "qr").mkdir()
        os.link(secret, self.user_home / "qr" / "x.png")
        self.refused(self.user_home / "qr" / "x.png", "2 hard links")
        shared = self.put("qr/shared.png", PNG)  # readable by others: a hard link is no matter
        os.link(shared, self.user_home / "qr" / "shared2.png")
        code, _, _ = self.run_cs("plugin", "telegram", "send", str(self.user_home / "qr" / "shared2.png"))
        self.assertEqual(code, 0)

    def test_fifo_not_waited_on(self):
        self.configure()
        os.mkfifo(self.files / "pipe")
        code, _, err = self.run_cs("plugin", "telegram", "send", str(self.files / "pipe"))
        self.assertEqual(code, 1)
        self.assertIn("not a file", err)

    def test_allow_private_cannot_be_everything(self):
        self.configure(allow_private=["~", "/"])
        code, _, err = self.run_cs("plugin", "telegram", "send", str(self.put("notes/a.txt", b"x", 0o600)))
        self.assertEqual(code, 1)
        self.assertIn("is the home directory or above it: ignored", err)
        self.assertIn("only its owner may read it", err)

    def test_name_not_in_utf8(self):
        self.configure()
        name = os.fsdecode(b"\xffnotes.txt")
        code, out, _ = self.run_cs("plugin", "telegram", "send", self.file(name))
        self.assertEqual(code, 0)
        self.assertEqual(self.methods(), ["sendDocument"])
        self.assertEqual(self.server.calls[0][3]["document"][1], b"hello")
        self.assertIn("notes.txt", out)

    def test_socks_proxy_said_plainly(self):
        self.configure(proxy="socks5://me:hunter2@127.0.0.1:1080")
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("socks5:// proxy: only http(s) proxies", err)
        self.assertNotIn("hunter2", err)
        self.configure()
        with mock.patch.dict(os.environ, {"https_proxy": "socks5h://127.0.0.1:1080", "no_proxy": ""}):
            code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertIn("https_proxy is a socks5h:// proxy", err)
        self.assertEqual(self.server.calls, [])

    def test_chats_when_updates_go_elsewhere(self):
        self.configure(chat_id=None)
        self.server.answers["getUpdates"] = [(409, {"ok": False, "error_code": 409,
            "description": "Conflict: can't use getUpdates method while webhook is active; use deleteWebhook to delete the webhook first"})]
        code, _, err = self.run_cs("plugin", "telegram", "chats")
        self.assertEqual(code, 1)
        self.assertNotIn("deleteWebhook", err)
        self.assertIn("Don't delete the webhook", err)

    def test_429_waited_out(self):
        self.configure()
        self.server.answers["sendMessage"] = [
            (429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 3", "parameters": {"retry_after": 3}}),
            (200, {"ok": True, "result": {}})]
        with mock.patch("time.sleep") as slept:
            code, _, _ = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 0)
        slept.assert_called_once_with(3)
        self.assertEqual(self.methods(), ["sendMessage", "sendMessage"])

    def test_api_error(self):
        self.configure()
        self.server.answers["sendMessage"] = [(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
        code, out, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("sendMessage: Unauthorized", err)

    def test_group_upgraded(self):
        self.configure()
        self.server.answers["sendMessage"] = [(400, {"ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup chat",
                                                     "parameters": {"migrate_to_chat_id": -1009876}})]
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("-1009876", err)

    def test_unreachable(self):
        self.configure(api_base="http://127.0.0.1:1")
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("sendMessage:", err)

    def test_not_configured(self):
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("@BotFather", err)
        self.assertIn(str(self.home / "telegram.json"), err)
        self.assertEqual(self.server.calls, [])

    def test_readable_by_others(self):
        self.configure(mode=0o644)
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("chmod 600", err)
        self.assertEqual(self.server.calls, [])

    def test_bad_token(self):
        self.configure(bot_token="not a token")
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("bot_token", err)

    def test_missing_file_sends_nothing(self):
        self.configure()
        code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi", self.file("a.png", PNG), str(self.files / "gone.pdf"))
        self.assertEqual(code, 1)
        self.assertIn("gone.pdf", err)
        self.assertEqual(self.server.calls, [])

    def test_nothing_to_send(self):
        self.configure()
        code, _, err = self.run_cs("plugin", "telegram", "send")
        self.assertEqual(code, 1)
        self.assertEqual(self.server.calls, [])

    def test_check(self):
        self.configure()
        self.server.answers["getMe"] = [(200, {"ok": True, "result": {"username": "my_bot"}})]
        self.server.answers["getChat"] = [(200, {"ok": True, "result": {"id": -1001234567890, "type": "supergroup", "title": "Inbox"}})]
        code, out, _ = self.run_cs("plugin", "telegram", "check")
        self.assertEqual(code, 0)
        self.assertIn("@my_bot", out)
        self.assertIn("Inbox", out)

    def test_chats_without_chat_id(self):
        self.configure(chat_id=None)
        self.server.answers["getUpdates"] = [(200, {"ok": True, "result": [
            {"update_id": 1, "my_chat_member": {"chat": {"id": -1001, "type": "supergroup", "title": "Inbox"}}},
            {"update_id": 2, "message": {"chat": {"id": 42, "type": "private", "first_name": "Me"}, "text": "/start"}}]})]
        code, out, _ = self.run_cs("plugin", "telegram", "chats")
        self.assertEqual(code, 0)
        self.assertIn("-1001\tsupergroup\tInbox", out)
        self.assertIn("42\tprivate\tMe", out)
        self.assertNotIn("offset", self.server.calls[0][2])  # nothing marked as seen

    def test_proxy(self):
        self.configure(proxy="http://127.0.0.1:1")
        with mock.patch.dict(os.environ, {"no_proxy": "", "NO_PROXY": ""}):
            code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)  # went to the (dead) proxy, not to the server
        self.assertEqual(self.server.calls, [])

    def test_prompt_for_the_secretary_only(self):
        self.assertIn("claudesec plugin telegram send", self.cs.plugin_prompt({}, self.cs.SECRETARY_WHO))
        worker = {"role": "worker", "name": "w", "agent": "claude", "discuss": False}
        self.assertEqual(self.cs.plugin_prompt({}, worker), "")

    def test_unexpected_error_scrubbed(self):
        self.configure()
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=RuntimeError(f"boom /bot{TOKEN}/sendMessage")):
            code, _, err = self.run_cs("plugin", "telegram", "send", "-t", "hi")
        self.assertEqual(code, 1)
        self.assertIn("boom /bot<token>/sendMessage", err)


if __name__ == "__main__":
    unittest.main()
