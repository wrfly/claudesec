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
| `worker_model` | `"claude-opus-5-5"` | Default model for workers; `claudesec spawn --model` overrides it per worker |
| `worker_permission_mode` | `"auto"` | Workers' `--permission-mode` |
| `cursor_worker_model` | `"gpt-5.6-sol[context=1m,reasoning=high,fast=false]"` (GPT-5.6 Sol 1M High) | Default model for `spawn --agent cursor` (Cursor's `agent` CLI). The first Cursor worker adds three hooks to `~/.cursor/hooks.json` (after a backup); they act only inside claudesec's Cursor workers. Set `"network": {"useHttp1ForAgent": true}` in `~/.cursor/cli-config.json`, or the agent stalls on HTTP/2 |
| `secretary_name` | `"secretary"` | The secretary's session name; workers send their reports to it |
| `tmux_session` | `"claudesec"` | The tmux session the secretary and workers live in |
| `cursor` | `"auto"` | Whether Cursor workers (`spawn --agent cursor`, a worker's `claudesec delegate`) can be used. `"auto"`: if the `agent` command is installed; `false`: off; `true`: on (still unavailable without `agent`). When unavailable, those two commands refuse and say why, and the secretary's and workers' rules leave out handing work to Cursor. `claudesec status` shows the current state at the end. After a change, `claudesec --restart`; workers pick it up when next started or woken |
| `dashboard` | `true` | Three panes to the right of the secretary, top to bottom: the decision queue (To ask, Waiting; Backlog folded into the last line), workers (status, how long ago, token use; shelved ones; reminders not yet due), and notices (things for you to do, registered with `claudesec notice`), refreshed every 3 s. `dashboard_side: "left"` puts the column on the secretary's left. Each item is folded to one line: click one to open it, click a title bar to open or fold them all; scroll with the mouse wheel (or ↑↓ / PgUp PgDn / g G with the pane focused). Shown when the window is 110+ columns wide; rearranged when the terminal is resized. Toggle with `claudesec dashboard on|off` |
| `language` | unset | `"en"` or `"zh"`; see [Language](#language) |
| `claude_config_dir` | automatic | Claude Code's config directory. If `CLAUDE_CONFIG_DIR` is set only in claude's own launcher script, claudesec processes started from tmux don't see it; the first hook run records it here. To have it right from the start, write it yourself (e.g. `"/home/me/.claude-alt"`) |

### Language

Everything claudesec shows or tells the models comes in English and Chinese:
- the panes, `claudesec status` and `claudesec queue`;
- the secretary's and workers' prompts (`prompts/en/` or `prompts/`);
- alerts, and the context hooks inject.

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

## Use

    claudesec                  # switch to tmux session "claudesec" (created if missing, resuming the last secretary)
    claudesec --new            # a fresh secretary (new conversation)
    claudesec --restart        # reload the secretary's prompt and hooks; the conversation and workers are untouched
    claudesec -d               # start in the background, don't switch to it
    claudesec dashboard off    # turn off the queue / workers / notices panes (on turns them back on)
    claudesec notice add --from W "say push in W's window"   # something for you to do; notice done N marks it done
    claudesec show 46          # the full text of decision-queue or notice item #46 (clicking it in the pane opens it too)

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
conventions settled in reviews), `prompts/worker.md`, and their English versions in `prompts/en/`.

These are not in the repo:
- `~/.claudesec/`, this machine's state: registry, history, lessons, decision queue.
- The secretary's Claude Code memory: preferences on this machine.

To bring the history along, copy `~/.claudesec/history/`. The session ids in its task files can be
`--resume`d only on the original machine.
