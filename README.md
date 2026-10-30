# Claude Secretary

English | [中文](README.zh.md)

A Claude secretary in tmux. You talk only to the main window. It hands work out to subagents or to
workers in their own tmux windows, takes their reports, sums them up, interrupts you only when it
needs you, and keeps a record you can still trace back a month later. The design is in [DESIGN.md](DESIGN.md)
(Chinese).

## Install (a new machine, from scratch)

You need tmux, python3 (3.8+) and Claude Code (`claude` on your PATH, logged in once).

    git clone git@github.com:wrfly/claudesec.git && cd claudesec
    ./install.sh          # links bin/claudesec into ~/.local/bin (make sure it's on PATH); changes no Claude Code settings
    claudesec                  # the first run creates ~/.claudesec/ (config.json, queue.md) and a tmux session "claudesec" with the secretary in it

For English, set the language before you start (see [Language](#language)):

    mkdir -p ~/.claudesec && echo '{"language": "en"}' > ~/.claudesec/config.json

The first time a worker starts in a new directory, Claude Code may ask whether you trust that
directory; answer it once in that window.

### Configuration: `~/.claudesec/config.json`

Written with defaults on the first run; fields you leave out keep their defaults. Run
`claudesec --restart` after a change.

| Field | Default | What it does |
|---|---|---|
| `secretary_model` | `null` | The secretary's model; `null` follows Claude Code's default. **Better pin it** (e.g. `"claude-opus-5-5"`): if the default becomes a model that needs extra credits, the secretary's start gets stuck on a confirmation dialog |
| `worker_model` | `"claude-opus-5-5"` | Default model for workers; `claudesec spawn --model` overrides it per worker. For a model that isn't Claude's (`deepseek-v4-pro-0813`, `glm-5.3`, `kimi-k3`…) use `--agent ttadk` |
| `worker_permission_mode` | `"auto"` | Workers' `--permission-mode` |
| `cursor_worker_model` | `"gpt-5.6-sol[context=1m,reasoning=high,fast=false]"` (GPT-5.6 Sol 1M High) | Default model for `spawn --agent cursor` (Cursor's `agent` CLI). The first Cursor worker adds three hooks to `~/.cursor/hooks.json` (after a backup); they act only inside claudesec's Cursor workers. Set `"network": {"useHttp1ForAgent": true}` in `~/.cursor/cli-config.json`, or the agent stalls on HTTP/2 |
| `codex_worker_model` | `null` (codex's own default) | Default model for `spawn --agent codex` (OpenAI's codex CLI, on its own login: ChatGPT, `OPENAI_API_KEY`, or a `model_provider` in `~/.codex/config.toml`). The first Codex worker (plain or through ttadk) adds three hooks to `~/.codex/hooks.json` (after a backup); they act only inside claudesec's Codex workers. Codex then asks once, in that worker's window, to trust them: pick "2. Trust all and continue" (claudesec never answers that one; it does answer the directory-trust question) |
| `ttadk_worker_model` | `"gpt-6.1-sol"` | Default model for `spawn --agent ttadk`, which runs an agent CLI through ByteDance's ttadk, on its endpoint (no other login). The model picks the CLI: `gpt-*`, `o<N>-*`, `codex-*` run Codex (`ttadk codex --mode ttadk-only`), any other (`deepseek-v4-pro-0813`, `glm-5.3`, `kimi-k3`…; `ttadk models` lists them) Claude Code (`ttadk claude`, an ordinary Claude Code worker but for remote control). A worker keeps the CLI it started on |
| `codex_args` | `["--dangerously-bypass-approvals-and-sandbox"]` | What codex (plain or through ttadk) runs with besides the model: by default no sandbox and no approval prompts (nobody watches the window). E.g. `["-s", "workspace-write", "-a", "never", "--add-dir", "~/.claudesec"]` for a sandbox that can still report |
| `delegate_agent` | `["ttadk", "codex", "cursor"]` | Which agent a worker's `claudesec delegate` starts unless it passes `--agent`: the first of these available here (a name or a list) |
| `stale_days` | `7` | The sweep points out open queue items and to-dos this many days old (again every as many days); `0` turns the age check off |
| `idle_ask_minutes` | `[5, 15, 30]` | The secretary idle this many minutes with queue items or to-dos for the user is woken to ask one; each further nudge waits for the next number, none after the last until the user speaks; `[]` turns it off |
| `runaway_pool_share` / `runaway_usd` / `runaway_quiet_minutes` | `0.3` / `20` / `60` | Alerts about a worker with no budget of its own: over this share of the budget window's cap, or over $N since a report more than M minutes ago |
| `cursor_max_running` | half the CPU cores | At most this many local Cursor workers at work at once (one idling at its prompt doesn't count; unset: half this machine's cores, at least 1; a number: that; `0`: no limit); `spawn --agent cursor`, `wake` and `delegate` past it refuse and say who is busy. `0`: no limit |
| `cursor_nice` | `10` | Cursor agents run under `nice -n` this, so they yield the CPU to everything else; `0`: normal priority |
| `secretary_name` | `"secretary"` | The secretary's session name; workers send their reports to it |
| `tmux_session` | `"claudesec"` | The tmux session the secretary and workers live in |
| `cursor` | `"auto"` | Whether Cursor workers (`spawn --agent cursor`, a worker's `claudesec delegate`) can be used. `"auto"`: if the `agent` command is installed; `false`: off; `true`: on (still unavailable without `agent`). When unavailable, those two commands refuse and say why, and the secretary's and workers' rules leave out handing work to Cursor. `claudesec status` shows the current state at the end. After a change, `claudesec --restart`; workers pick it up when next started or woken |
| `codex` / `ttadk` | `"auto"` | Whether plain Codex / ttadk workers can be used, as `cursor` above. `codex` `"auto"`: `codex` installed and logged in; `ttadk` `"auto"`: the `ttadk` command installed (with `codex` too for its Codex workers). Each agent of `agents/*.py` has such a switch, named after it; see `agents/README.md` for adding one |
| `dashboard` | `true` | Four panes to the right of the secretary, top to bottom: to-dos (things for you to do, registered with `claudesec notice`), the decision queue (To ask, Waiting; Backlog folded into the last line), workers (status, how long ago, token use and today's spend; shelved ones; on the last row, what the secretary and everything spent today), and scheduled tasks (`claudesec remind`s not yet due), refreshed every 3 s. `dashboard_side: "left"` puts the column on the secretary's left. Each item is folded to one line: click one to open it, click a title bar to open or fold them all; scroll with the mouse wheel (or ↑↓ / PgUp PgDn / g G with the pane focused). Shown when the window is 110+ columns wide; rearranged when the terminal is resized. Toggle with `claudesec dashboard on|off` |
| `remote_control` | `false` | Start sessions with AgentLodge's `claude --rc`, to follow them, send messages and answer approvals on its web page. `"secretary"`: the secretary only; `"all"`: the secretary and Claude workers (not Cursor or Codex workers, nor Claude workers on a ttadk model). Needs the `claude` on `PATH` to be AgentLodge's launcher. Claude Code then warns "WARNING: Loading development channels" at every start; claudesec picks "I am using this for local development". Takes effect at start only: the secretary with `claudesec --restart` (conversation kept); a running worker after shelve and wake, new ones right away. The page gets the history from the channel server (`remote-control channel`, a process of its own), which reads the transcript: the secretary's can pass 100 MB (112 MB on 2026-10-30), and the page then sits on "loading" or hangs; claude's own resume does not go through it. With a transcript that big, leave it off for the secretary |
| `language` | unset | `"en"` or `"zh"`; see [Language](#language) |
| `claude_config_dir` | automatic | Claude Code's config directory. If `CLAUDE_CONFIG_DIR` is set only in claude's own launcher script, claudesec processes started from tmux don't see it; the first hook run records it here. To have it right from the start, write it yourself (e.g. `"/home/me/.claude-alt"`) |

### Language

What claudesec shows comes in English and Chinese:
- the panes, `claudesec status` and `claudesec queue`;
- alerts, and the context hooks inject.

The secretary's and workers' prompts (`prompts/`) are Chinese only: the models follow them whatever language you talk to them in.

Set `"language": "en"` (or `"zh"`) in `~/.claudesec/config.json`, then run `claudesec --restart`.
Workers switch when they are next started or woken.

Unset, claudesec follows the `language` in Claude Code's own settings.json: English if that names English, else Chinese.
The system locale isn't consulted.

`queue.md` and `lessons.md` are read in either language; new ones are written in the configured one.

### With tmux-resurrect / continuum

claudesec's session must not be saved by resurrect. A restore would bring it back as bare
`claude --resume` panes without claudesec's prompts and hooks, and could also knock out the window
the secretary has just started in.

claudesec sets `@resurrect-skip on` on its session; your save hook has to drop sessions marked that way.
`contrib/resurrect-agent-commands.py` is such a hook. It also saves claude / codex / cursor agent panes in
other sessions as commands that resume their conversations. Add to your tmux config:

    set -g @resurrect-processes 'claude agent codex cursor-agent'
    set -g @resurrect-hook-post-save-layout 'python3 /path/to/claudesec/contrib/resurrect-agent-commands.py'

If you already have your own save hook, make it skip the sessions marked `on` in
`tmux list-sessions -F '#{session_name} #{@resurrect-skip}'` (their `pane` and `window` lines).

To check: `python3 contrib/resurrect-agent-commands.py --check` should show claudesec's panes as
`(not saved: @resurrect-skip)`.

### Shift+Enter for a new line

`claudesec` turns on tmux's `extended-keys` (and `extkeys` for `xterm*` terminals), so Shift+Enter
reaches Claude Code inside tmux; reattach once after the first start. The terminal you run tmux in must
send Shift+Enter apart from Enter: Ghostty and foot do with no setup (WezTerm speaks its own keyboard
protocol, which tmux 3.4 doesn't pass on, and needs a key mapping); GNOME Terminal and other VTE terminals
don't, whatever tmux does. There, Alt+Enter or Ctrl+J make a new line.

kitty doesn't do modifyOtherKeys, so through tmux it takes one line in `kitty.conf` (reload or restart kitty):

    map shift+enter send_text all \x1b[13;2u

The catch: Shift+Enter now always sends that sequence, so a shell outside tmux may print junk for it.

## Use

    claudesec                  # switch to tmux session "claudesec" (created if missing, resuming the last secretary)
    claudesec --new            # a fresh secretary (new conversation)
    claudesec --restart        # reload the secretary's prompt and hooks; the conversation and workers are untouched
    claudesec -d               # start in the background, don't switch to it
    claudesec budget --until 20:00 --usd 100   # a spending window for all workers: wrap-up notice at 85%, frozen at 100%
    claudesec budget --add 50 | --until 23:00 | --from 09:00 --until 18:00 | --off   # extend, book the next window, end
    claudesec cost --today      # spend today by worker and model, at list prices (claudesec cost NAME --since 3h)
    claudesec dashboard off    # turn off the to-do / queue / workers / scheduled panes (on turns them back on)
    claudesec notice add --from W "say push in W's window"   # something for you to do; notice done N marks it done
    claudesec show 46          # the full text of decision-queue or to-do item #46 (clicking it in the pane opens it too)
    claudesec stale            # open queue items and to-dos that look out of date (closed worker, merged MRs, old)
    claudesec queue list --older 7   # open items that have waited a week or more
    claudesec queue doctor     # check queue.md (line-number prefixes, doubled sections, numbers twice); --fix repairs
    claudesec model W M        # switch worker W to model M (restarts it, conversation kept); the way for Cursor and Codex workers

Every item in the queue pane has a number: reply to the secretary "12 A" or "15 no need" to answer that item.

Then just talk in the secretary's window. Workers appear as windows in the same session, named after
their task. Switch over to watch one if you like, or don't. Your own tmux sessions are untouched; switch
back any time.

What you occasionally do:
- When the secretary starts a worker it gives a one-line receipt; say no if it's wrong.
- If it forwarded something to the wrong worker, say "that wasn't for A, it was for B".
- It asks one thing at a time; what you have no time for, it keeps for later.
- The first time you talk to it on a working day, it runs a review (`claudesec review`): it tidies its memory,
  turns the lessons it collected into a constitution diff, and asks you about it as one decision, yes or no.
  Say "review" to have one right away.

When you exit the secretary (`/exit`, or just close the window), claudesec puts all workers away and leaves
no processes behind. The next `claudesec` brings them back with their history. After a reboot, run
`claudesec` once.

To set something aside for now, tell the secretary "set it aside" and it shelves it; "carry on with X" wakes it again.

Claude sessions you already have running can be handed to the secretary too: say "adopt blog", or "adopt blog
once it stops". Adopting restarts that session in its window, with its conversation history kept.

## Taking it to another machine

What's in the repo applies to anyone: `prompts/secretary.md` (the secretary's constitution, including the
conventions settled in reviews), and `prompts/worker.md`.

These are not in the repo:
- `~/.claudesec/`, this machine's state: registry, history, lessons, decision queue.
- The secretary's Claude Code memory: preferences on this machine.

To bring the history along, copy `~/.claudesec/history/`. The session ids in its task files can be
`--resume`d only on the original machine.

## Working on claudesec itself

`install.sh` links `~/.local/bin/claudesec` to this checkout's `bin/claudesec`, so the secretary, every
worker's hooks and the dashboard run whatever that file holds at the moment: a half-done edit or a rebase
stopped on a conflict breaks all of them at once. Make changes, rebases and merges in a separate
worktree (`git worktree add ../claudesec-dev`), run the tests there
(`python3 -m unittest discover -s tests`), and only then bring this checkout up to date, in one
fast-forward (`git merge --ff-only`).
