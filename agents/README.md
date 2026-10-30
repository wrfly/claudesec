# agents/: the agents a worker can run

Claude Code is built into `bin/claudesec` (class `ClaudeCode`). Every other agent is one file here,
an adapter: `cursor.py` (Cursor's `agent` CLI), `codex.py` (OpenAI's codex CLI on its own login),
`ttadk.py` (ByteDance's ttadk launcher, running Codex or Claude Code by model).

At start claudesec execs each `agents/*.py` into its own namespace, in file-name order, skipping
files that start with `_`. A file sees everything `bin/claudesec` defines (`Agent`, `config()`,
`tmux()`, `pane_text()`, `worker_dir()`, `tr()`, …) and the files before it (ttadk.py subclasses
codex.py's `Codex`). No imports needed beyond those.

An agent whose command isn't installed is off: `ttadk.py` costs nothing on a machine without ttadk.

## Adding one

1. A file `agents/<name>.py` with a subclass of `Agent`, decorated `@register_agent`. `name` is what
   `--agent` takes and what the registry records.
2. Class attributes (see `class Agent` in `bin/claudesec` for all of them and their defaults):
   - `label`: how messages name it ("Cursor worker").
   - `describe`: what the worker rules (`prompts/worker-external.md`, `{{AGENT}}`) say it runs as.
   - `commands`: what must be on PATH for it to be available.
   - `model_key`: the config key holding its default model. Add that key to `DEFAULT_CONFIG`.
   - `resume_cmd`: how a user reopens its conversation by hand, given the id.
   - `protocol` and `hook_events`: below.
   - `interrupt_key` (default Escape), `nags` (reminders to report before giving up, default 1),
     `up` (a regex: its screen once it is up at its prompt after a resume).
3. Methods:
   - `command(name, model, d, cfg, resume=None, prompt=True)`, required: the shell command that
     runs the agent, starting with `self.env(name)` (which sets `CLAUDESEC_WORKER`), resuming
     conversation `resume` if given, with `self.first_message(d)` at the end when `prompt`.
   - `lacking()`: something else it needs and doesn't have (a login), or "".
   - `answer_dialog(name, target, screen)`: answer a start-up question nobody would; return truthy
     when it did. Questions that are the user's (trusting hooks) are not answered:
     `dialog_hint(screen)` says what the user should do instead.
   - `conversation_exists(sid)`, `follow(sid, cwd, old)`: whether a conversation is still on disk,
     and moving it when the worker's directory moved, if the agent files conversations by directory.
   - `interrupt(name, w)`, `room(...)`, `preflight()`, `status_note()`, `error_text(w)`: as needed.
4. Hooks. The agent must be able to run a command at session start, at each user prompt, and when a
   turn ends, and read JSON from it. `hook_events` maps its event names to `"start"`, `"prompt"`,
   `"stop"`; `install()` writes `claudesec hook agent-<kind> <protocol>` into `hooks_file()`, each
   entry guarded so it does nothing outside a claudesec worker, after a backup of the file. The
   logic is shared (`hook_agent_start` / `hook_agent_prompt` / `hook_agent_stop`); the adapter only
   reads and answers the payloads:
   - `session_id(data)`: the conversation id in the start payload.
   - `start_reply(rules)`: the JSON that hands the agent the worker rules as context.
   - `stop_event(data)`: (how the turn ended: "completed" / "error" / other, reminders sent so far).
   - `stop_reply(reminder)`: the JSON that lets the turn end, or sends the reminder and goes on.
   An agent that speaks an existing protocol (the way ttadk's Codex speaks codex's) sets the same
   `protocol`; the stop hook looks the adapter up by it.
5. An agent that runs others (ttadk) overrides `runtime(model, recorded)` to return the adapter
   for a model; those adapters set `runtime_name`, which the registry records, and are not
   registered themselves.
6. Config: the key named after the agent turns it on or off, `"auto"` (default: if installed),
   `true`, `false`. List it in `delegate_agent` if `claudesec delegate` may start it.
7. Prompts: `<!-- if <name> --> … <!-- else --> … <!-- end <name> -->` keeps a part only where the
   agent is available. Blocks nest; markers sit on lines of their own.
8. Tests: `tests/test_agents.py`. The test loader points every adapter's `hooks_file()` into a
   temp dir; a test must never write the user's real hooks files.
