"""Telegram: send the user text and files through a bot, to one chat (a group, usually).

    claudesec telegram send [-t TEXT | -t -] [--document] [FILE ...]
    claudesec telegram check     the bot and the chat it sends to
    claudesec telegram chats     the chats the bot has seen lately, to find the chat_id

Only sends: it doesn't read the chat for messages. Config is $CLAUDESEC_HOME/telegram.json, mode 600:
    {"bot_token": "123456:ABC...", "chat_id": -1001234567890}
optional "proxy" ("http://127.0.0.1:7890"; else the https_proxy environment applies) and "api_base".
The token is never printed: not in output, errors or tracebacks.
Enable: ln -s <repo>/plugins/telegram ~/.claudesec/plugins/telegram
"""
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API_BASE = "https://api.telegram.org"
TEXT_MAX = 4096            # a message, in UTF-16 code units as Telegram counts them
CAPTION_MAX = 1024
PHOTO_MAX = 10 << 20       # sendPhoto's limit; bigger pictures go as files
FILE_MAX = 50 << 20        # the Bot API's upload limit
PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp"}
TIMEOUT = 120
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
CHAT_RE = re.compile(r"^(-?\d+|@\w+)$")
ANY_TOKEN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")
_token = []                # the token loaded, so scrub() can take it out of any message


class Failed(Exception):
    def __init__(self, text, code=None):
        super().__init__(text)
        self.code = code


def tr(zh, en):
    return cs.tr(zh, en)


def scrub(text):
    text = str(text)
    for t in _token:
        text = text.replace(t, "<token>")
    return ANY_TOKEN.sub("<token>", text)


def conf_path():
    return cs.HOME / "telegram.json"


def setup_text(path):
    return tr(f"""Telegram 还没配置（{path} 不存在）。要用户自己做（token 别贴进聊天）：
1. Telegram 里找 @BotFather，发 /newbot 建一个 bot，记下它给的 token（形如 123456:ABC…）
2. 把 bot 拉进要收东西的群，在群里发一句 /start@bot的用户名
3. 写配置，权限 600：
   umask 077; cat > {path} <<'EOF'
   {{"bot_token": "123456:ABC…", "chat_id": -100…}}
   EOF
   不知道 chat_id：先只写 bot_token，跑 claudesec telegram chats 看 bot 见过的群和它们的 id，再补上
4. claudesec telegram check 验证""",
              f"""Telegram isn't set up ({path} doesn't exist). For the user to do (don't paste the token into a chat):
1. In Telegram, ask @BotFather for /newbot and keep the token it gives (like 123456:ABC…)
2. Add the bot to the group that should get things, and send /start@the_bot_username there
3. Write the config, mode 600:
   umask 077; cat > {path} <<'EOF'
   {{"bot_token": "123456:ABC…", "chat_id": -100…}}
   EOF
   No chat_id yet: write just bot_token, run claudesec telegram chats for the chats the bot has seen, then add it
4. claudesec telegram check to try it""")


def load_conf(need_chat=True):
    path = conf_path()
    if not path.exists():
        raise Failed(setup_text(path))
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise Failed(tr(f"{path} 别人也能读（权限 {mode:o}）：先 chmod 600 {path}",
                        f"{path} is readable by others (mode {mode:o}): chmod 600 {path} first"))
    try:
        conf = json.loads(path.read_text())
    except ValueError as e:  # the message has a position, not the file's text
        raise Failed(tr(f"{path} 不是合法 JSON：{e}", f"{path} isn't valid JSON: {e}")) from None
    token = str(conf.get("bot_token") or "").strip()
    if not TOKEN_RE.match(token):
        raise Failed(tr(f"{path} 的 bot_token 不对：应该形如 123456:ABC…（@BotFather 给的）",
                        f"bot_token in {path} doesn't look right: it should be like 123456:ABC… (from @BotFather)"))
    _token[:] = [token]
    chat = str(conf.get("chat_id") or "").strip()
    if need_chat and not CHAT_RE.match(chat):
        raise Failed(tr(f"{path} 缺 chat_id（群的 id 形如 -100…）：claudesec telegram chats 列出 bot 见过的群",
                        f"{path} has no chat_id (a group's is like -100…): claudesec telegram chats lists the ones the bot has seen"))
    return {"token": token, "chat_id": chat, "proxy": conf.get("proxy"),
            "api_base": str(conf.get("api_base") or API_BASE).rstrip("/")}


def opener(conf):
    if conf.get("proxy"):
        p = str(conf["proxy"])
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener()  # https_proxy / no_proxy from the environment


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        out += str(value).encode() + b"\r\n"
    for key, path in files.items():
        name = re.sub(r'["\r\n\\]', "_", path.name)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def call(conf, method, fields, files=None):
    """The Bot API's result, waiting out a 429 up to three times; Failed with its description else."""
    url = f"{conf['api_base']}/bot{conf['token']}/{method}"
    if files:
        body, ctype = multipart(fields, files)
    else:
        body, ctype = urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        try:
            with opener(conf).open(req, timeout=TIMEOUT) as r:
                got = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                got = json.load(e)
            except ValueError:
                got = {"ok": False, "error_code": e.code, "description": f"HTTP {e.code}"}
        except (OSError, ValueError) as e:  # refused, DNS, timeout, not JSON
            raise Failed(scrub(f"{method}: {e}")) from None
        if got.get("ok"):
            return got.get("result")
        params = got.get("parameters") or {}
        if got.get("error_code") == 429 and params.get("retry_after") and attempt < 3:
            time.sleep(min(int(params["retry_after"]), 60))
            continue
        why = scrub(f"{method}: {got.get('description') or 'failed'}")
        if params.get("migrate_to_chat_id"):
            why += tr(f"（群升级成了超级群，新的 chat_id 是 {params['migrate_to_chat_id']}：改 {conf_path()}）",
                      f" (the group became a supergroup; its chat_id is now {params['migrate_to_chat_id']}: change {conf_path()})")
        raise Failed(why, got.get("error_code"))


def u16(text):
    return len(text.encode("utf-16-le")) // 2


def fit(text, limit):
    """How many characters of text fit in limit UTF-16 units."""
    n = 0
    for i, ch in enumerate(text):
        n += 2 if ord(ch) > 0xFFFF else 1
        if n > limit:
            return i
    return len(text)


def chunks(text, limit=TEXT_MAX):
    """text in messages of at most limit, cut between lines where it can be."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if u16(cur + line) > limit:
            out.append(cur)
            cur = ""
        while u16(line) > limit:
            cut = fit(line, limit)
            out.append(line[:cut])
            line = line[cut:]
        cur += line
    out.append(cur)
    return [c.strip("\n") for c in out if c.strip()]


def send_file(conf, path, caption, as_document):
    fields = {"chat_id": conf["chat_id"]}
    if caption:
        fields["caption"] = caption
    if not as_document and path.suffix.lower() in PHOTO_TYPES and path.stat().st_size <= PHOTO_MAX:
        try:
            call(conf, "sendPhoto", fields, {"photo": path})
            return "photo"
        except Failed as e:
            if e.code != 400:  # a picture Telegram won't take as a photo (its size, its shape) goes as a file
                raise
    call(conf, "sendDocument", fields, {"document": path})
    return "file"


def cmd_send(args):
    conf = load_conf()
    text = sys.stdin.read() if args.text == "-" else (args.text or "")
    text = text.strip()
    files = [Path(f).expanduser() for f in args.files]
    if not text and not files:
        raise Failed(tr("没东西可发：给 -t 文字（-t - 从 stdin 读）或文件", "nothing to send: give -t TEXT (-t - reads stdin) or files"))
    for f in files:  # every one checked before anything is sent
        if not f.is_file():
            raise Failed(tr(f"{f}：不是文件", f"{f}: not a file"))
        size = f.stat().st_size
        if not size:
            raise Failed(tr(f"{f}：空文件，Telegram 不收", f"{f}: empty, Telegram won't take it"))
        if size > FILE_MAX:
            raise Failed(tr(f"{f}：{size >> 20}MB，超过 Bot API 的 50MB 上限", f"{f}: {size >> 20}MB, over the Bot API's 50MB limit"))
    caption = text if len(files) == 1 and text and u16(text) <= CAPTION_MAX else None
    if text and not caption:
        for part in chunks(text):
            call(conf, "sendMessage", {"chat_id": conf["chat_id"], "text": part})
        print(tr(f"已发：文字（{len(text)} 字）", f"sent: text ({len(text)} chars)"))
    for f in files:
        kind = send_file(conf, f, caption, args.document)
        with_caption = tr("，带文字", ", with the text") if caption else ""
        print(tr(f"已发：{'图片' if kind == 'photo' else '文件'} {f.name}{with_caption}", f"sent: {kind} {f.name}{with_caption}"))


def chat_name(chat):
    return chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) \
        or ("@" + chat["username"] if chat.get("username") else "")


def cmd_check(args):
    conf = load_conf()
    me = call(conf, "getMe", {})
    chat = call(conf, "getChat", {"chat_id": conf["chat_id"]})
    print(tr(f"bot @{me.get('username')} → {chat.get('type')}「{chat_name(chat)}」（{chat.get('id')}）：能发",
             f"bot @{me.get('username')} → {chat.get('type')} \"{chat_name(chat)}\" ({chat.get('id')}): ready"))


def cmd_chats(args):
    """The chats in the bot's pending updates (Telegram keeps them a day). Reads without an offset,
    so nothing is marked as seen."""
    conf = load_conf(need_chat=False)
    seen = {}
    for update in call(conf, "getUpdates", {"timeout": 0}) or []:
        for value in update.values():
            if isinstance(value, dict) and isinstance(value.get("chat"), dict):
                seen[value["chat"]["id"]] = value["chat"]
    if not seen:
        print(tr("bot 最近没见过任何群：把它拉进群，在群里发 /start@bot的用户名，再跑一次",
                 "the bot hasn't seen any chat lately: add it to the group, send /start@the_bot_username there, run this again"))
    for cid, chat in seen.items():
        print(f"{cid}\t{chat.get('type')}\t{chat_name(chat)}")


def run(fn):
    def go(args):
        try:
            fn(args)
        except Failed as e:
            print(f"claudesec telegram: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:  # no traceback: one could carry the URL, and the URL has the token
            print(f"claudesec telegram: {type(e).__name__}: {scrub(e)}", file=sys.stderr)
            sys.exit(1)
    return go


def commands(sub):
    s = sub.add_parser("telegram", help="send the user text and files through a Telegram bot")
    t = s.add_subparsers(dest="action", required=True)
    x = t.add_parser("send")
    x.add_argument("files", nargs="*")
    x.add_argument("-t", "--text", help="the text; - reads it from stdin")
    x.add_argument("--document", action="store_true", help="pictures as files too (not recompressed)")
    x.set_defaults(fn=run(cmd_send))
    t.add_parser("check").set_defaults(fn=run(cmd_check))
    t.add_parser("chats").set_defaults(fn=run(cmd_chats))


def prompt(cfg, who):
    if who["role"] != "secretary":
        return ""
    return """## 发到 Telegram

用户让你把东西「发给我」（一段文字、文件、截图、二维码）时，用 `claudesec telegram send` 发到配好的 Telegram 群里：
- 文件：`claudesec telegram send /绝对路径/a.png /绝对路径/b.pdf`。png / jpg / webp 按图片发（会压缩），别的按文件发；
  要原图（截图要看清字、二维码要扫）加 `--document`
- 文字：`-t '一句话'`；长的、带引号的从 stdin 读：`claudesec telegram send -t - [文件…] <<'EOF'` … `EOF`。
  文字配一个文件时当它的说明一起发
- 文件在 worker 那里：让 worker 在汇报里给绝对路径，你来发
发完给一行收据「发到 Telegram 了：a.png」。报「还没配置」就把它打印的步骤列成用户的待办，别自己去找 token、也别读 telegram.json。
"""
