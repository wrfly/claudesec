# Codex workers: OpenAI's codex CLI in a window, logged in on its own. Loaded into bin/claudesec
# (see agents/README.md); ttadk.py runs the same CLI through ttadk. Its hooks are global, in
# ~/.codex/hooks.json; its Stop hook blocks the turn's end as Claude Code's does, once
# (stop_hook_active). An interrupted turn runs no Stop. Codex runs a hook only once the user has
# trusted it: the first worker's window asks ("Hooks need review"), and the user answers that
# once; the commands never change, so neither does the trust.

# Before codex_args: no update offer at start (a dialog nobody would answer), no full-access
# warning (the user chose full access).
CODEX_FIXED_ARGS = ["-c", "check_for_update_on_startup=false", "-c", "notice.hide_full_access_warning=true"]
CODEX_TRUST = re.compile(r"Do you trust the contents of this directory")
CODEX_RESUME_DIR = re.compile(r"Choose working directory to resume this session")
# Up and at its prompt: the banner of a new session, or (a resumed one shows its history, no
# banner) the footer under the input, "gpt-6.1-sol default · ~/proj".
CODEX_UP = re.compile(r"OpenAI Codex \(v|^\s+\S.* · [~/]\S*\s*\Z", re.M)


@register_agent
class Codex(Agent):
    name, label, describe = "codex", "Codex", "Codex（OpenAI 的 codex CLI）"
    commands = ("codex",)
    model_key = "codex_worker_model"
    resume_cmd = "codex resume"
    send_busy = "Codex takes it into the turn under way"
    protocol = "codex"
    hook_events = {"SessionStart": "start", "UserPromptSubmit": "prompt", "Stop": "stop"}
    up = CODEX_UP

    def lacking(self):
        """Its own login: ChatGPT's or an API key, or a provider of its own in config.toml."""
        home = codex_home()
        if (home / "auth.json").exists() or os.environ.get("OPENAI_API_KEY"):
            return ""
        with contextlib.suppress(OSError):
            if re.search(r"(?m)^model_provider\s*=", (home / "config.toml").read_text()):
                return ""
        return tr(f"codex 没登录（没有 {home}/auth.json）", f"codex isn't logged in (no {home}/auth.json)")

    def launcher(self, model):
        return ["codex", *([] if model in (None, "default") else ["-m", model])]

    def command(self, name, model, d, cfg, resume=None, prompt=True):
        """extra goes last: `resume <id>` takes no options after it."""
        return (self.env(name) + " ".join(shlex.quote(a) for a in [
            *self.launcher(model), *CODEX_FIXED_ARGS, *cfg.get("codex_args", []), *(["resume", resume] if resume else [])])
            + (self.first_message(d) if prompt else ""))

    def answer_dialog(self, name, target, screen):
        """Codex's start-up questions a worker's window can't leave open. Trust the directory (the
        user's call, 2026-10-30: claudesec says yes for a worker); and on resume, the directory the
        window was opened in, which wake chose (a moved directory, --dir), not the one recorded.
        "Hooks need review" is not answered: trusting hooks is the user's to do."""
        if CODEX_TRUST.search(screen) and "Yes, continue" in screen:
            tmux("send-keys", "-t", target, "1", check=False)
        elif CODEX_RESUME_DIR.search(screen) and "Use current directory" in screen:
            tmux("send-keys", "-t", target, "2", check=False)
        else:
            return False
        time.sleep(1)
        return True

    def dialog_hint(self, screen):
        return ("This is Codex asking, once, to trust claudesec's hooks (worker rules and reports run "
                "through them): the user picks \"2. Trust all and continue\" in that window."
                if "Hooks need review" in screen else "")

    def interrupt(self, name, w):
        key = super().interrupt(name, w)
        (worker_dir(name) / "busy").unlink(missing_ok=True)  # no Stop hook runs: its end is marked here
        (worker_dir(name) / "ended-unreported").write_text(now())
        return key

    def conversation_exists(self, sid):
        return bool(codex_session_file(sid))

    # follow: none. Codex finds a session by id from anywhere, and its resume asks which
    # directory (answer_dialog).

    def hooks_file(self):
        return codex_home() / "hooks.json"

    def hook_entry(self, event, command):
        entry = {"type": "command", "command": command}
        if event == "SessionStart":
            entry["additionalContextLimit"] = 0  # the whole rules prompt: past ~2500 tokens it'd be a preview
        return {"hooks": [entry]}

    def start_reply(self, rules):
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": rules}}

    def stop_event(self, data):
        return "completed", 1 if data.get("stop_hook_active") else 0

    def stop_reply(self, reminder=None):
        return {"decision": "block", "reason": reminder} if reminder else {}  # it wants JSON either way


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_session_file(sid):
    return next(iter(sorted((codex_home() / "sessions").glob(f"*/*/*/rollout-*-{sid}.jsonl"))), None) if sid else None
