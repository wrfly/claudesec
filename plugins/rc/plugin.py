"""rc: the sessions that join the web page, and the box they start with.

Sessions started with --rc are joined to the page, to go on with them there. The secretary (the
user, 2026-11-06: "你启动claude的时候，用 --rc") and the oncall worker (2026-11-10: "重启并保持oncall
会话，连接到remote") are; other workers are started as before. Through the AgentLodge wrapper
(~/.agentlodge/bin/claude) --rc is taken out and replaced with its own channel to the page; every
other argument (--resume included) is passed on unchanged.

An --rc session starts on a confirmation box of its own:

    WARNING: Loading development channels ...
    Channels: server:remote
    ❯ 1. I am using this for local development
      2. Exit

Nobody may be looking at the window (a woken worker), so answer_dialog picks 1 — but only when the
box lists server:remote as its only channel and the cursor is on that line. Any other channel, more
than one, or the cursor elsewhere is the user's to answer.

Config: "rc_workers": ["oncall"], the workers started with --rc. The secretary always is.
"""
import re

BOX = "Loading development channels"
CHANNELS = re.compile(r"(?m)^[^\w]*Channels:\s*(.+)$")
ON_ONE = re.compile(r"(?m)^[^\w]*❯\s*1\.\s*I am using this for local development\b")
EDGE = re.compile(r"[\s│┃|║╎╏]+$")
OURS = "server:remote"
DEFAULT_RC_WORKERS = ["oncall"]


def rc_workers(cfg):
    names = cfg.get("rc_workers", DEFAULT_RC_WORKERS)
    return [names] if isinstance(names, str) else list(names)


def claude_args(cfg, who):
    if who["role"] == "secretary" or who.get("name") in rc_workers(cfg):
        return ["--rc"]
    return []


def answer_dialog(target, screen):
    """The dev-channels box, when there is nothing in it for the user to decide: server:remote, the
    only channel, with the cursor already on "1. I am using this for local development". Enter.

    Reads the pane itself: pane_text takes a leading ❯ for Claude Code's input line and may drop
    what follows as a suggestion, which is the one line here that decides."""
    if screen is not None and BOX not in screen:
        return False
    text = cs.tmux("capture-pane", "-p", "-t", target, check=False)
    if BOX not in text:
        return False
    listed = CHANNELS.findall(text)
    if len(listed) != 1 or [c.strip() for c in EDGE.sub("", listed[0]).split(",")] != [OURS]:
        return False
    if not ON_ONE.search(text):
        return False
    cs.tmux("send-keys", "-t", target, "Enter")
    return True
