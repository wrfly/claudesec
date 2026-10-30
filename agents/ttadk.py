# ttadk workers: ByteDance's ttadk launcher, which runs an agent CLI against its own endpoint (no
# ChatGPT or claude.ai login). Loaded into bin/claudesec after codex.py (see agents/README.md).
# The model picks the agent it runs: gpt-*, o<N>-* and codex-* models run Codex (`ttadk codex`),
# any other (deepseek-…, glm-…, kimi-…, claude-…) Claude Code (`ttadk claude`). A worker keeps
# the one it started on: its conversation belongs to it. `ttadk models` lists what there is.

TTADK_CODEX_MODEL = re.compile(r"(gpt|o\d|codex)\b", re.I)


@register_agent
class Ttadk(Agent):
    name, label = "ttadk", "ttadk"
    commands = ("ttadk",)
    model_key = "ttadk_worker_model"

    def ready(self):
        return tr(f"{shutil.which('ttadk')}，默认 {config()['ttadk_worker_model']}；gpt-* 跑 Codex，其余跑 Claude Code",
                  f"{shutil.which('ttadk')}, default {config()['ttadk_worker_model']}; gpt-* runs Codex, the rest Claude Code")

    def runtime(self, model, recorded=None):
        kind = recorded or ("codex" if TTADK_CODEX_MODEL.match(model or "") else "claude")
        return TTADK_RUNTIMES[kind]


class TtadkCodex(Codex):
    name, runtime_name = "ttadk", "codex"
    describe = "Codex（OpenAI 的 codex CLI，经 ttadk 启动）"
    commands = ("ttadk", "codex")
    model_key = "ttadk_worker_model"
    resume_cmd = "ttadk codex -- resume"
    start_wait = 20  # ttadk takes a few seconds first

    def lacking(self):
        return ""  # ttadk's endpoint, not codex's own login

    def launcher(self, model):
        return ["ttadk", "codex", "--mode", "ttadk-only", "-m", model, "--skip-check", "--"]


class TtadkClaude(ClaudeCode):
    """ttadk passes on what follows `--` and merges its endpoint's env into our --settings file."""
    name, runtime_name = "ttadk", "claude"
    label = "Claude Code (ttadk)"
    commands = ("ttadk", "claude")
    model_key = "ttadk_worker_model"
    remote_control = False  # needs a claude.ai login, which a ttadk session doesn't use
    start_wait = 20

    def available(self):
        return Agent.available(self)

    def launcher(self, model):
        return ["ttadk", "claude", "-m", model, "--skip-check", "--"]


TTADK_RUNTIMES = {"codex": TtadkCodex(), "claude": TtadkClaude()}
