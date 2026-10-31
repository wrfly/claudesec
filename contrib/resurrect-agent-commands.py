#!/usr/bin/env python3
"""Point tmux-resurrect at the same Claude, Cursor agent, and Codex chats.

tmux-resurrect remembers layout and the raw process command. A raw "claude"
or "agent" starts a brand new chat. This rewrites each saved pane command so
restore runs the resume command for the chat that pane had open.

A session with the @resurrect-skip option set is left out of the save
altogether: whoever set it (claudesec, for one) brings that session back itself.
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
LOG_PATH = Path.home() / ".tmux" / "resurrect-agents.log"


def log(message):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def read_cmdline(pid):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw if part]


def read_comm(pid):
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def read_environ(pid):
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return {}
    env = {}
    for item in raw:
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def child_map():
    children = defaultdict(list)
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        end = stat.rfind(")")
        if end < 0:
            continue
        fields = stat[end + 2 :].split()
        if len(fields) < 2:
            continue
        try:
            ppid = int(fields[1])
        except ValueError:
            continue
        children[ppid].append(pid)
    return children


def descendants(root, children, limit=6):
    found = []
    queue = [(root, 0)]
    seen = set()
    while queue:
        pid, depth = queue.pop(0)
        if pid in seen or depth > limit:
            continue
        seen.add(pid)
        found.append(pid)
        if depth == limit:
            continue
        for child in children.get(pid, ()):
            queue.append((child, depth + 1))
    return found


def uuid_from_args(args):
    for index, arg in enumerate(args):
        if arg in ("--resume", "--continue", "resume") and index + 1 < len(args):
            if UUID_RE.match(args[index + 1]):
                return args[index + 1]
        if arg.startswith("--resume="):
            value = arg.split("=", 1)[1]
            if UUID_RE.match(value):
                return value
    return None


def claude_session_id(pid, args, env):
    candidates = []
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        candidates.append(Path(config_dir) / "sessions" / f"{pid}.json")
    home = Path.home()
    candidates.append(home / ".claude" / "sessions" / f"{pid}.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session_id = data.get("sessionId")
        if isinstance(session_id, str) and UUID_RE.match(session_id):
            return session_id
    return uuid_from_args(args)


def cursor_chat_id(pid, args):
    explicit = uuid_from_args(args)
    if explicit:
        return explicit
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        fds = list(fd_dir.iterdir())
    except OSError:
        return None
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if not target.endswith("/store.db"):
            continue
        parts = target.split("/")
        if "chats" not in parts or ".cursor" not in parts:
            continue
        chat_id = parts[-2]
        if UUID_RE.match(chat_id):
            return chat_id
    return None


def codex_session_id(pid, args):
    explicit = uuid_from_args(args)
    if explicit:
        return explicit
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        fds = list(fd_dir.iterdir())
    except OSError:
        return None
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if "codex" not in target:
            continue
        match = re.search(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            target,
        )
        if match:
            return match.group(0)
    return None


def resume_command(pid):
    args = read_cmdline(pid)
    if not args:
        return None
    base = os.path.basename(args[0])
    comm = read_comm(pid)
    env = read_environ(pid)
    invoked = env.get("CURSOR_INVOKED_AS", "")

    if base == "claude" or comm == "claude":
        session_id = claude_session_id(pid, args, env)
        if session_id:
            return f"claude --resume {session_id}"
        return None

    if base in ("agent", "cursor-agent") or invoked in ("agent", "cursor-agent"):
        chat_id = cursor_chat_id(pid, args)
        if chat_id:
            return f"agent --resume {chat_id}"
        return None

    if base == "codex" or comm == "codex":
        session_id = codex_session_id(pid, args)
        if session_id:
            return f"codex resume {session_id}"
        return None
    return None


def resume_command_for_pane(pane_pid, children):
    for pid in descendants(pane_pid, children):
        command = resume_command(pid)
        if command:
            return command
    return None


def pane_index():
    import subprocess

    output = subprocess.check_output(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_pid}",
        ],
        text=True,
    )
    mapping = {}
    for line in output.splitlines():
        session, window, pane, pid = line.split("\t")
        if pid.isdigit():
            mapping[(session, window, pane)] = int(pid)
    return mapping


def skipped_sessions():
    import subprocess

    output = subprocess.check_output(
        ["tmux", "list-sessions", "-F", "#{session_name}\t#{@resurrect-skip}"],
        text=True,
    )
    skipped = set()
    for line in output.splitlines():
        session, _, flag = line.partition("\t")
        if flag not in ("", "0", "off"):
            skipped.add(session)
    return skipped


def rewrite(path):
    original = Path(path).read_text(encoding="utf-8", errors="replace")
    panes = pane_index()
    children = child_map()
    skipped = skipped_sessions()
    rewritten = []
    changes = 0
    dropped = 0
    for line in original.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        fields = body.split("\t")
        if fields and fields[0] in ("pane", "window", "grouped_session") and len(fields) > 2 and (
            fields[1] in skipped or (fields[0] == "grouped_session" and fields[2] in skipped)
        ):
            dropped += 1
            continue
        if fields and fields[0] == "state":
            fields = [field if index == 0 or field not in skipped else "" for index, field in enumerate(fields)]
            rewritten.append("\t".join(fields) + newline)
            continue
        if not fields or fields[0] != "pane" or len(fields) < 11:
            rewritten.append(line)
            continue
        session, window, pane = fields[1], fields[2], fields[5]
        pane_pid = panes.get((session, window, pane))
        command = resume_command_for_pane(pane_pid, children) if pane_pid else None
        if not command:
            rewritten.append(line)
            continue
        fields[10] = ":" + command
        rewritten.append("\t".join(fields) + newline)
        changes += 1
        log(f"{session}:{window}.{pane} -> {command}")
    Path(path).write_text("".join(rewritten), encoding="utf-8")
    if dropped:
        log(f"left out {dropped} line(s) of @resurrect-skip session(s) {', '.join(sorted(skipped))}")
    log(f"rewrote {changes} pane(s) in {path}")
    return changes


def check():
    panes = pane_index()
    children = child_map()
    skipped = skipped_sessions()
    for (session, window, pane), pid in sorted(panes.items(), key=lambda item: (item[0][0], int(item[0][1]), int(item[0][2]))):
        command = "(not saved: @resurrect-skip)" if session in skipped else resume_command_for_pane(pid, children)
        print(f"{session}:{window}.{pane}\t{command or '-'}")


def main(argv):
    if len(argv) == 2 and argv[1] == "--check":
        check()
        return 0
    if len(argv) != 2:
        print("usage: resurrect-agent-commands.py SAVE_FILE | --check", file=sys.stderr)
        return 0
    try:
        rewrite(argv[1])
    except Exception as exc:  # keep the tmux save even if rewriting fails
        log(f"error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
