# ARK workers: Claude Code on a BytePlus / Volcengine ARK endpoint (DeepSeek and others), through
# bin/claude-ark-deepseek, which translates Anthropic Messages into ARK's OpenAI Chat Completions on
# the fly. The model is the endpoint ID (ep-...), which ARK requires instead of a model ID.
# Loaded into bin/claudesec after ttadk.py (see agents/README.md).


@register_agent
class Ark(ClaudeCode):
    name, label = "ark", "Claude Code (ARK)"
    describe = "Claude Code（跑在 ARK 端点上的模型，经 claude-ark-deepseek 的本地协议转换代理）"
    commands = ("claude-ark-deepseek", "claude")
    model_key = "ark_worker_model"
    resume_cmd = "claude-ark-deepseek --resume"

    def available(self):
        return Agent.available(self)

    def ready(self):
        return tr(f"{shutil.which('claude-ark-deepseek')}，端点里配的模型",
                  f"{shutil.which('claude-ark-deepseek')}, the model its config names")

    def launcher(self, model):
        # The wrapper reads the endpoint ID from ARK_MODEL, the key from its config file or the env.
        return ["env", "ARK_MODEL=" + model, "claude-ark-deepseek"]

    def preflight(self):
        """Refuse before a window opens when the wrapper has no key to send."""
        if os.environ.get("ARK_API_KEY"):
            return
        conf = Path(os.path.expanduser("~/.config/claude-ark-deepseek.json"))
        try:
            key = json.loads(conf.read_text()).get("api_key")
        except (OSError, ValueError):
            key = None
        if not key:
            sys.exit(f"an ARK worker needs an API key: set ARK_API_KEY, or put \"api_key\" in {conf}")
