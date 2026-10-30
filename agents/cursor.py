# Cursor workers: Cursor's `agent` CLI in a window. Loaded into bin/claudesec (see agents/README.md).
# Its hooks are global, in ~/.cursor/hooks.json; its stop hook asks for a missing report with a
# follow-up message, up to three times (loop_count). A Ctrl+C ends the turn with status "error",
# so `claudesec interrupt` leaves a mark that tells the two apart.

CURSOR_FALLBACK_MODEL = "gpt-5.6-sol-high"
AGENT_START = "[claudesec] Cursor agent, model "
CURSOR_AT_WORK = {"STARTING", "PROGRESS"}


@register_agent
class Cursor(Agent):
    name, label, describe = "cursor", "Cursor", "Cursor agent"
    commands = ("agent",)
    model_key = "cursor_worker_model"
    resume_cmd = "agent --resume"
    interrupt_key = "C-c"  # its Escape only drops a queued message
    send_busy = "queued until its turn ends"
    protocol = "cursor"
    hook_events = {"sessionStart": "start", "beforeSubmitPrompt": "prompt", "stop": "stop"}
    nags = 3
    up = re.compile(r"Cursor Agent|Add a follow-up|ctrl\+c to stop")
    up_after = 4

    def room(self, what, but=None, prefix=""):
        """Refuse when cursor_max_running Cursor workers are already at work: each agent is a local
        process tree that can take whole cores (the user's CPU, 2026-10-24)."""
        limit = cursor_limit()
        busy = cursor_busy(but)
        if limit and len(busy) >= limit:
            sys.exit(f"{prefix}{what}: {len(busy)} Cursor workers are already at work ({', '.join(busy)}), the limit "
                     f"(cursor_max_running in config.json) is {limit}. Wait for one to finish, or do it with a "
                     f"subagent or yourself.")

    def preflight(self):
        check_cursor_http1()

    def status_note(self):
        return (tr(f" · 在跑 {len(cursor_busy())}/{cursor_limit()}", f" · at work {len(cursor_busy())}/{cursor_limit()}")
                if cursor_limit() else "")

    def command(self, name, model, d, cfg, resume=None, prompt=True):
        return cursor_command(name, model, *(["--resume", resume] if resume else []), d=d if prompt else None)

    def spawn_command(self, name, model, d, cfg):
        # `hook cursor-launch` runs the agent: it retries or falls back when the model is refused,
        # and records a failure instead of a STARTING that never ends.
        return cursor_launch_command(name, model)

    def confirm_resume(self, name, pane, d, sid):
        """A resumed Cursor chat fires no sessionStart, so there is no session id to wait for:
        the agent drawing its screen and staying up is as good as it gets."""
        screen = ""
        for i in range(20):
            time.sleep(1)
            screen = pane_text(pane, suggestion="drop")
            if (d / "exit").exists() or tmux("display", "-p", "-t", pane, "#{pane_dead}", check=False).strip() != "0":
                return "agent exited", screen
            if i >= self.up_after and self.up.search(screen):
                return "ok", screen
        return "agent did not come up within 20s", screen

    def interrupt(self, name, w):
        (worker_dir(name) / "interrupted").write_text(now())  # its stop hook then sees status "error"
        return super().interrupt(name, w)

    def conversation_exists(self, sid):
        return cursor_chat_exists(sid)

    def follow(self, sid, cwd, old=None):
        """Cursor keeps chats by the md5 of the directory; one is moved only out of old, the
        directory the worker is known to have left."""
        chats = Path.home() / ".cursor" / "chats"
        src = chats / hashlib.md5(str(old).encode()).hexdigest() / sid if old else None
        dst = chats / hashlib.md5(str(cwd).encode()).hexdigest() / sid
        if not src or not src.is_dir() or src == dst or dst.exists():
            return None
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return f"chat {sid} moved to {dst.parent}"

    def hooks_file(self):
        return Path.home() / ".cursor" / "hooks.json"

    def empty_hooks(self):
        return {"version": 1, "hooks": {}}

    def session_id(self, data):
        return data.get("session_id") or data.get("conversation_id")

    def start_reply(self, rules):
        return {"additional_context": rules}

    def stop_event(self, data):
        return data.get("status"), data.get("loop_count", 0)

    def stop_reply(self, reminder=None):
        return {"followup_message": reminder} if reminder else None

    def error_text(self, w):
        return cursor_error_text(w.get("window"))

    def hooks(self):
        # Before `hook agent-*`: what ~/.cursor/hooks.json may still call until the next install.
        return {"cursor-session-start": lambda: hook_agent_start("cursor"),
                "cursor-prompt": lambda: hook_agent_prompt("cursor"),
                "cursor-stop": lambda: hook_agent_stop("cursor"),
                "cursor-launch": hook_cursor_launch}


def cursor_launch_command(name, model):
    """What a spawned Cursor worker's window runs. nice goes after the VAR=value assignments:
    put before them, it takes the first one for the command to run, and the window dies at once."""
    return (f"CLAUDESEC_MODEL={shlex.quote(model)} CLAUDESEC_HOME={shlex.quote(str(HOME))} {cursor_nice()}"
            f"{shlex.quote(str(SELF))} hook cursor-launch {shlex.quote(name)}")


def cursor_command(name, model, *extra, d=None):
    """The worker's agent command; with d, its first message comes from d/initial.md."""
    env = f"CLAUDESEC_WORKER={shlex.quote(name)} CLAUDESEC_HOME={shlex.quote(str(HOME))}"
    return (env + " " + cursor_nice() + " ".join(shlex.quote(a) for a in ["agent", "--model", model, "--trust", *extra])
            + (f' "$(cat {shlex.quote(str(d / "initial.md"))})"' if d else ""))


def cursor_busy(but=None):
    """Cursor workers whose agent is at work on this machine: a live window and a turn under way.
    One idling at its prompt (done, waiting on the user) costs next to no CPU and doesn't count."""
    alive = alive_windows()
    return sorted(n for n, w in read_registry()[1].items()
                  if w.get("agent") == "cursor" and n != but and w["status"] in CURSOR_AT_WORK and w.get("window") in alive)


def cursor_limit():
    """cursor_max_running as it applies: a number set in config.json as is (0: no limit), else half
    this machine's cores, at least one (the user, 2026-10-26: the devbox can run plenty)."""
    value = config().get("cursor_max_running")
    if value is None or str(value).lower() == "auto":
        return max(1, (os.cpu_count() or 2) // 2)
    return int(value)


def cursor_nice():
    """`nice -n N ` before a Cursor agent's command, or nothing."""
    n = int(config().get("cursor_nice") or 0)
    return f"nice -n {n} " if n > 0 and shutil.which("nice") else ""


def check_cursor_http1():
    """Over HTTP/2 the agent stalls on agentn.us.api5.cursor.sh on the Mac's network, whatever the
    model; say so rather than leave a worker that never answers. Only a warning: the file is the
    user's. The devbox (Linux) talks HTTP/2 fine (two long tool-using runs, 2026-10-22)."""
    if sys.platform != "darwin":
        return
    path = Path.home() / ".cursor" / "cli-config.json"
    try:
        on = (json.loads(path.read_text()).get("network") or {}).get("useHttp1ForAgent") is True
    except (OSError, ValueError, AttributeError):
        on = False
    if not on:
        print(f"WARNING: {path} does not set \"network\": {{\"useHttp1ForAgent\": true}}. Without it the "
              "agent talks HTTP/2 to agentn.us.api5.cursor.sh and, on this network, stalls on "
              "\"Connection stalled\" with any model, so the worker never gets through a turn. "
              "To fix: back the file up, add that key under \"network\" (leave the rest as it is), "
              "then restart the worker: close and spawn it again, or shelve and wake it.", flush=True)


def cursor_chat_exists(chat):
    return bool(chat) and any((Path.home() / ".cursor" / "chats").glob(f"*/{chat}"))


def cursor_error_text(window):
    """The error a Cursor agent printed at the end of a failed turn ("Error: Model Blocked"): its
    stop hook says only "error". The screen may lag the hook by a moment."""
    if not window:
        return ""
    for _ in range(3):
        lines = [l.strip() for l in pane_text(window, suggestion="drop").splitlines() if l.strip()]
        hits = [l for l in lines[-40:] if re.search(r"\b(Error|error|ERROR)\b|Blocked|blocked", l)]
        if hits:
            return " ".join(hits[-1].split())[:200]
        time.sleep(1)
    return ""


def hook_cursor_launch(name):
    """What a spawned Cursor worker's window runs: its agent, in the foreground. Cursor now and
    then answers a model variant string with "Cannot use this model" and exits, though the same
    string works a minute later (devbox, 2026-10-22). Refused, the model is tried once more; refused
    again, CURSOR_FALLBACK_MODEL stands in for it, which the worker's summary and log say. Any
    other failure is left to hook_agent_exit."""
    model = os.environ.get("CLAUDESEC_MODEL") or config()["cursor_worker_model"]
    first = (worker_dir(name) / "initial.md").read_text()
    pane = os.environ.get("TMUX_PANE", "")
    env = {**os.environ, "CLAUDESEC_WORKER": name, "CLAUDESEC_HOME": str(HOME)}
    env.pop("CLAUDESEC_MODEL", None)
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C (`claudesec interrupt`) is the agent's

    def run(m):
        print(AGENT_START + m, flush=True)
        return subprocess.run(["agent", "--model", m, "--trust", first], env=env,
                              preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL)).returncode

    def refused():  # only what the last run printed
        screen = pane_text(pane, suggestion="drop") if pane else ""
        return "Cannot use this model" in screen.rsplit(AGENT_START, 1)[-1]

    rc = run(model)
    if rc and refused():
        time.sleep(5)
        rc = run(model)
        if rc and refused() and model != CURSOR_FALLBACK_MODEL:
            note = tr(f"模型兜底：{model} 被拒两次，改用 {CURSOR_FALLBACK_MODEL}",
                      f"model fallback: {model} refused twice, using {CURSOR_FALLBACK_MODEL}")
            with registry() as reg:
                w = reg["workers"].get(name)
                if w is not None:
                    w.update(model=CURSOR_FALLBACK_MODEL, last_summary=note)
            append_log(name, "model-fallback", note)
            rc = run(CURSOR_FALLBACK_MODEL)
    os.environ["CLAUDESEC_RC"] = str(rc)
    hook_agent_exit(name)
