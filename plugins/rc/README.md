# plugins/rc: the sessions that join the web page

A session started with `--rc` is joined to Claude's web page, to go on with it there. Through the
AgentLodge wrapper (`~/.agentlodge/bin/claude`) `--rc` is taken out and replaced with its own channel
to the page; every other argument (`--resume` included) is passed on unchanged.

It's a plugin (see the comment above `load_plugins` in `bin/claudesec`), off until you link it in. It adds:

| Hook | What |
|---|---|
| `claude_args` | `--rc` for the secretary, always, and for the workers in `rc_workers` |
| `answer_dialog` | the `Loading development channels` box, answered only when there is nothing to decide (below) |

## The box

An `--rc` session starts on:

    WARNING: Loading development channels ...
    Channels: server:remote
    ❯ 1. I am using this for local development
      2. Exit

`answer_dialog` presses Enter when — and only when — `server:remote` is the box's **only** channel and
the cursor is on `1. I am using this for local development`. Any other channel, more than one, or the
cursor elsewhere is left alone: claudesec says the window is stuck and the user answers it.

## Setting it up

    repo=$(dirname "$(dirname "$(readlink -f ~/.local/bin/claudesec)")")   # this checkout
    mkdir -p ~/.claudesec/plugins && ln -s "$repo/plugins/rc" ~/.claudesec/plugins/rc
    claudesec plugin             # rc → …/plugins/rc, its hooks

Then in `~/.claudesec/config.json`, if the `--rc` workers aren't the default `["oncall"]`:

    {"rc_workers": ["oncall", "other"]}

## Tests

`tests/test_rc_plugin.py` links this directory into a temporary `CLAUDESEC_HOME` and answers boxes:
the sole `server:remote` with the cursor on 1, and every other shape left to the user.
`python3 -m unittest discover -s tests`.
