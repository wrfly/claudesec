"""Telegram: send the user text and files through a bot, to one chat (a group, usually).

    claudesec plugin telegram send [-t TEXT | -t -] [--document] [FILE ...]
    claudesec plugin telegram check     the bot and the chat it sends to
    claudesec plugin telegram chats     the chats the bot has seen lately, to find the chat_id

Only sends: it doesn't read the chat for messages. Config is $CLAUDESEC_HOME/telegram.json, mode 600:
    {"bot_token": "123456:ABC...", "chat_id": -1001234567890}
optional "proxy" ("http://127.0.0.1:7890"; else the https_proxy environment applies) and "api_base".
The token is never printed: not in output, errors or tracebacks. Credentials are refused
(the config itself, ~/.ssh and the like, private keys, a file with the token in it), and so is a file
only its owner may read unless it's in a directory of "allow_private" in the config: the secretary runs
this without a permission prompt, on paths a worker may have reported.
Enable: ln -s <repo>/plugins/telegram ~/.claudesec/plugins/telegram
"""
import json
import mimetypes
import os
import re
import stat
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
TEXT_PARTS_MAX = 3         # more than this many messages of text: one message.txt instead
WAIT_MAX = 60              # seconds of 429 waiting in all, past which it gives up
PHOTO_REFUSED = re.compile(r"PHOTO_|IMAGE_|dimension", re.I)  # sendPhoto's 400s that a file would get past
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
CHAT_RE = re.compile(r"^(-?\d+|@\w+)$")
ANY_TOKEN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")
_token = []                # the token loaded, so scrub() can take it out of any message


class Failed(Exception):
    def __init__(self, text, code=None, description=""):
        super().__init__(text)
        self.code, self.description = code, description


def tr(zh, en):
    return cs.tr(zh, en)


def scrub(text):
    text = str(text)
    for t in _token:
        text = text.replace(t, "<token>")
    return ANY_TOKEN.sub("<token>", text)


def conf_path():
    return cs.HOME / "telegram.json"


def shown(path):
    """A path printable whatever bytes its name has."""
    return str(path).encode("utf-8", "replace").decode("utf-8")


# Where credentials live, under the home directory: never sent, whatever allow_private says.
CREDENTIAL_PATHS = (
    ".ssh", ".gnupg", ".password-store", ".pki", ".aws", ".azure", ".kube", ".docker", ".terraform.d",
    ".config/gcloud", ".config/gh", ".config/hub", ".config/rclone", ".config/op", ".config/sops",
    ".local/share/keyrings", ".mozilla", ".config/google-chrome", ".config/chromium", ".config/BraveSoftware",
    ".netrc", ".git-credentials", ".pgpass", ".npmrc", ".pypirc", ".vault-token", ".cargo/credentials",
    ".cargo/credentials.toml", ".codex/auth.json",
)
# File names that hold a secret wherever they are.
CREDENTIAL_NAMES = re.compile(
    r"^(id_(rsa|dsa|ecdsa|ed25519)(_sk)?|\.credentials\.json|\.git-credentials|\.netrc|\.pgpass|\.env(\..+)?)$"
    r"|\.(key|p12|pfx|kdbx|jks|keystore)$", re.I)
PRIVATE_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY")


def homes():
    return {Path.home(), Path.home().resolve()}


def under(path, top):
    try:
        path.relative_to(top)
        return True
    except ValueError:
        return False


def private_dirs(conf):
    """allow_private from the config, as resolved directories. One that is the home directory or
    above it is dropped: that would let every private file through."""
    want = conf.get("allow_private")
    if want is None:
        return []
    if not isinstance(want, list) or not all(isinstance(d, str) for d in want):
        raise Failed(tr(f"{conf_path()} 的 allow_private 要写成目录的列表，比如 [\"~/qr\"]",
                        f"allow_private in {conf_path()} must be a list of directories, like [\"~/qr\"]"))
    out = []
    for d in want:
        d = Path(d).expanduser()
        if not d.is_absolute():
            raise Failed(tr(f"{conf_path()} 的 allow_private 里 {shown(d)} 不是绝对路径（可以用 ~/ 开头）",
                            f"allow_private in {conf_path()}: {shown(d)} isn't an absolute path (~/ is fine)"))
        d = d.resolve()
        if any(under(h, d) for h in homes()):
            print(tr(f"claudesec plugin telegram: allow_private 里的 {shown(d)} 是家目录或更上层，不算",
                     f"claudesec plugin telegram: allow_private's {shown(d)} is the home directory or above it: ignored"), file=sys.stderr)
            continue
        out.append(d)
    return out


def secret_in(data, conf):
    """Why these bytes (a file's, or the text) must not go out, or None."""
    if PRIVATE_KEY.search(data):
        return tr("里面有私钥", "it has a private key in it")
    if conf["token"].encode() in data:
        return tr("里面有这个 bot 的 token", "it has this bot's token in it")
    return None


def open_once(path):
    """(the file it really is, its fstat, its bytes), all from one open: what is checked is what is
    sent, whatever happens to the path in between. Failed if it isn't a regular file or is too big."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))  # a FIFO doesn't hang the open
    except OSError as e:
        raise Failed(tr(f"{shown(path)}：打不开（{e.strerror}）", f"{shown(path)}: can't open it ({e.strerror})")) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Failed(tr(f"{shown(path)}：不是文件", f"{shown(path)}: not a file"))
        if st.st_size > FILE_MAX:
            raise Failed(tr(f"{shown(path)}：{st.st_size >> 20}MB，超过 Bot API 的 50MB 上限",
                            f"{shown(path)}: {st.st_size >> 20}MB, over the Bot API's 50MB limit"))
        try:  # where the open file is, not where the path points now
            real = Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            real = path.resolve()
        with os.fdopen(os.dup(fd), "rb") as f:
            data = f.read(FILE_MAX + 1)
    finally:
        os.close(fd)
    if len(data) > FILE_MAX:
        raise Failed(tr(f"{shown(path)}：超过 Bot API 的 50MB 上限", f"{shown(path)}: over the Bot API's 50MB limit"))
    if not data:
        raise Failed(tr(f"{shown(path)}：空文件，Telegram 不收", f"{shown(path)}: empty, Telegram won't take it"))
    return real, st, data


def refused(path, real, st, data, conf):
    """Why the file open_once read must not go out through here, or None. The secretary runs this
    without a permission prompt, on paths a worker may have reported, so: never a credential; a file
    only its owner may read only from a directory the user put in allow_private, and not a hard link."""
    with_conf = conf_path()
    if with_conf.exists() and os.path.samestat(st, with_conf.stat()):
        return tr("这是 Telegram 的配置文件（里面有 token）", "it's the Telegram config (the token is in it)")
    for p in {path.absolute(), real}:
        if CREDENTIAL_NAMES.search(p.name):
            return tr(f"{p.name} 这种文件名放的是凭据", f"a file named {p.name} holds a credential")
        for h in homes():
            hit = next((c for c in CREDENTIAL_PATHS if under(p, h / c)), None)
            if hit:
                return tr(f"在 ~/{hit} 里，那是放凭据的地方", f"it's in ~/{hit}, where credentials live")
    why = secret_in(data, conf)
    if why:
        return why
    mode = st.st_mode & 0o777
    if not mode & 0o077:
        if not any(under(real, d) for d in conf["allow_private"]):
            return tr(f"只有属主能读（权限 {mode:o}），像是私密文件；要能发，用户自己把它所在的目录加进 {conf_path()} 的 allow_private",
                      f"only its owner may read it (mode {mode:o}): it looks private. To have files like it sent, the user"
                      f" adds its directory to allow_private in {conf_path()} themselves")
        if st.st_nlink > 1:  # the same file may sit somewhere else under another name
            return tr(f"只有属主能读，又有 {st.st_nlink} 个硬链接：可能是别处私密文件的另一个名字",
                      f"only its owner may read it and it has {st.st_nlink} hard links: it may be a private file from elsewhere")
    return None


def setup_text(path):
    return tr(f"""Telegram 还没配置（{path} 不存在）。要用户自己做（token 别贴进聊天）：
1. Telegram 里找 @BotFather，发 /newbot 建一个 bot，记下它给的 token（形如 123456:ABC…）
2. 把 bot 拉进要收东西的群，在群里发一句 /start@bot的用户名
3. 写配置，权限 600：
   umask 077; cat > {path} <<'EOF'
   {{"bot_token": "123456:ABC…", "chat_id": -100…}}
   EOF
   不知道 chat_id：先只写 bot_token，跑 claudesec plugin telegram chats 看 bot 见过的群和它们的 id，再补上
4. claudesec plugin telegram check 验证""",
              f"""Telegram isn't set up ({path} doesn't exist). For the user to do (don't paste the token into a chat):
1. In Telegram, ask @BotFather for /newbot and keep the token it gives (like 123456:ABC…)
2. Add the bot to the group that should get things, and send /start@the_bot_username there
3. Write the config, mode 600:
   umask 077; cat > {path} <<'EOF'
   {{"bot_token": "123456:ABC…", "chat_id": -100…}}
   EOF
   No chat_id yet: write just bot_token, run claudesec plugin telegram chats for the chats the bot has seen, then add it
4. claudesec plugin telegram check to try it""")


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
        raise Failed(tr(f"{path} 缺 chat_id（群的 id 形如 -100…）：claudesec plugin telegram chats 列出 bot 见过的群",
                        f"{path} has no chat_id (a group's is like -100…): claudesec plugin telegram chats lists the ones the bot has seen"))
    out = {"token": token, "chat_id": chat, "proxy": conf.get("proxy"),
           "api_base": str(conf.get("api_base") or API_BASE).rstrip("/"), "allow_private": conf.get("allow_private")}
    out["allow_private"] = private_dirs(out)
    return out


def opener(conf):
    """urllib speaks only to http(s) proxies (CONNECT); a socks one would fail with a puzzling error."""
    if conf.get("proxy"):
        proxies = {"http": str(conf["proxy"]), "https": str(conf["proxy"])}
        where = f"{conf_path()} proxy"
    else:
        proxies = urllib.request.getproxies()  # https_proxy / no_proxy from the environment
        where = "https_proxy"
    p = proxies.get("https")
    scheme = urllib.parse.urlsplit(p if "://" in p else "http://" + p).scheme if p else "http"
    if scheme not in ("http", "https"):  # just the scheme: the rest may hold a password
        raise Failed(tr(f"{where} 是 {scheme}:// 代理：只支持 http(s) 代理（socks 的话用它的 http 端口，比如 clash 的混合端口）",
                        f"{where} is a {scheme}:// proxy: only http(s) proxies work (for a socks one use its http port, e.g. clash's mixed port)"))
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        out += str(value).encode() + b"\r\n"
    for key, (name, data) in files.items():
        name = re.sub(r'["\r\n\\]', "_", name)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode("utf-8", "replace")  # a name not in UTF-8
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def call(conf, method, fields, files=None):
    """The Bot API's result, waiting out 429s up to WAIT_MAX seconds in all; Failed with its description else."""
    url = f"{conf['api_base']}/bot{conf['token']}/{method}"
    if files:
        body, ctype = multipart(fields, files)
    else:
        body, ctype = urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded"
    waited = 0
    while True:
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
        wait = int(params.get("retry_after") or 0) if got.get("error_code") == 429 else 0
        if wait and waited + wait <= WAIT_MAX:
            time.sleep(wait)
            waited += wait
            continue
        why = scrub(f"{method}: {got.get('description') or 'failed'}")
        if wait:
            why += tr(f"（Telegram 要再等 {wait} 秒：过一会儿再发）", f" (Telegram wants another {wait}s: send it later)")
        if params.get("migrate_to_chat_id"):
            why += tr(f"（群升级成了超级群，新的 chat_id 是 {params['migrate_to_chat_id']}：改 {conf_path()}）",
                      f" (the group became a supergroup; its chat_id is now {params['migrate_to_chat_id']}: change {conf_path()})")
        raise Failed(why, got.get("error_code"), got.get("description") or "")


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


def send_file(conf, path, data, caption, as_document):
    fields = {"chat_id": conf["chat_id"]}
    if caption:
        fields["caption"] = caption
    if not as_document and path.suffix.lower() in PHOTO_TYPES and len(data) <= PHOTO_MAX:
        try:
            call(conf, "sendPhoto", fields, {"photo": (path.name, data)})
            return "photo"
        except Failed as e:  # a picture Telegram won't take as a photo (its size, its shape) goes as a file
            if e.code != 400 or not PHOTO_REFUSED.search(e.description):
                raise
    call(conf, "sendDocument", fields, {"document": (path.name, data)})
    return "file"


def cmd_send(args):
    conf = load_conf()
    text = sys.stdin.read() if args.text == "-" else (args.text or "")
    text = text.strip()
    files = [Path(f).expanduser() for f in args.files]
    if not text and not files:
        raise Failed(tr("没东西可发：给 -t 文字（-t - 从 stdin 读）或文件", "nothing to send: give -t TEXT (-t - reads stdin) or files"))
    dont = tr("别复制、别改权限、别改配置绕过。什么都没发",
              "don't copy it, change its mode or edit the config to get around this. Nothing was sent")
    why = secret_in(text.encode("utf-8", "replace"), conf)
    if why:
        raise Failed(tr(f"文字不经这里发：{why}。要发就请用户自己从 Telegram 发；{dont}",
                        f"the text doesn't go out this way: {why}. If it should be sent, the user sends it from Telegram themselves; {dont}"))
    data = {}
    for f in files:  # every one checked before anything is sent, and sent as it was read then
        real, st, data[f] = open_once(f)
        why = refused(f, real, st, data[f], conf)
        if why:
            raise Failed(tr(f"{shown(f)} 不经这里发：{why}。要发就请用户自己从 Telegram 发；{dont}",
                            f"{shown(f)} doesn't go out this way: {why}. If it should be sent, the user sends it from Telegram themselves; {dont}"))
    caption = text if len(files) == 1 and text and u16(text) <= CAPTION_MAX else None
    if text and not caption:
        parts = chunks(text)
        if len(parts) > TEXT_PARTS_MAX:  # dozens of messages would run into 429s; one file reads better
            call(conf, "sendDocument", {"chat_id": conf["chat_id"]}, {"document": ("message.txt", text.encode("utf-8", "replace"))})
            print(tr(f"已发：文字（{len(text)} 字，太长，作为 message.txt）", f"sent: text ({len(text)} chars, too long: as message.txt)"), flush=True)
        else:
            for i, part in enumerate(parts, 1):
                call(conf, "sendMessage", {"chat_id": conf["chat_id"], "text": part})
                of = f" {i}/{len(parts)}" if len(parts) > 1 else ""
                print(tr(f"已发：文字{of}（{len(part)} 字）", f"sent: text{of} ({len(part)} chars)"), flush=True)
    for f in files:
        kind = send_file(conf, f, data[f], caption, args.document)
        with_caption = tr("，带文字", ", with the text") if caption else ""
        print(tr(f"已发：{'图片' if kind == 'photo' else '文件'} {shown(f.name)}{with_caption}",
                 f"sent: {kind} {shown(f.name)}{with_caption}"), flush=True)


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
    try:
        updates = call(conf, "getUpdates", {"timeout": 0, "limit": 100})
    except Failed as e:
        if e.code != 409:
            raise
        raise Failed(tr("bot 的消息另有去处（设了 webhook，或别的程序在收），这里读不到它见过的群。别为此删 webhook，"
                        "那可能是别的程序在用：群的 chat_id 换个办法查（比如在群里临时加一个查 id 的 bot）",
                        "the bot's updates go elsewhere (a webhook, or another program polling), so its chats can't be read here."
                        " Don't delete the webhook for this, another program may need it: find the group's chat_id another way"
                        " (e.g. add a bot that tells chat ids to the group for a moment)")) from None
    for update in updates or []:
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
            print(f"claudesec plugin telegram: {shown(e)}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:  # no traceback: one could carry the URL, and the URL has the token
            print(f"claudesec plugin telegram: {type(e).__name__}: {scrub(e)}", file=sys.stderr)
            sys.exit(1)
    return go


def commands(sub):
    """claudesec plugin telegram send / check / chats"""
    x = sub.add_parser("send", help="text and files to the chat")
    x.add_argument("files", nargs="*")
    x.add_argument("-t", "--text", help="the text; - reads it from stdin")
    x.add_argument("--document", action="store_true", help="pictures as files too (not recompressed)")
    x.set_defaults(fn=run(cmd_send))
    sub.add_parser("check", help="the bot and the chat it sends to").set_defaults(fn=run(cmd_check))
    sub.add_parser("chats", help="the chats the bot has seen lately").set_defaults(fn=run(cmd_chats))


def prompt(cfg, who):
    if who["role"] != "secretary":
        return ""
    return """## 发到 Telegram

用户让你把东西「发给我」（一段文字、文件、截图、二维码）时，用 `claudesec plugin telegram send` 发到配好的 Telegram 群里：
- 文件：`claudesec plugin telegram send /绝对路径/a.png /绝对路径/b.pdf`。png / jpg / webp 按图片发（会压缩），别的按文件发；
  要原图（截图要看清字、二维码要扫）加 `--document`
- 文字：`-t '一句话'`；长的、带引号的从 stdin 读：`claudesec plugin telegram send -t - [文件…] <<'EOF'` … `EOF`。
  文字配一个文件时当它的说明一起发
- 文件在 worker 那里：让 worker 在汇报里给绝对路径，你来发
- 凭据发不了：telegram.json、~/.ssh 这类凭据目录、私钥、含 token 的文件都拒；只有属主能读的（600）只在用户列进
  allow_private 的目录里才发。被拒就告诉用户、请用户自己从 Telegram 发；别复制到别处、别 chmod、别改 telegram.json 绕过，
  worker 汇报里给的路径同样如此
发完给一行收据「发到 Telegram 了：a.png」。报「还没配置」就把它打印的步骤列成用户的待办，别自己去找 token、也别读 telegram.json。
"""
