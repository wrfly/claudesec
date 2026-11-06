# plugins/telegram: send yourself things through a Telegram bot

Ask the secretary to "send me" something (a file, a screenshot, a QR code, a piece of text) and it sends
it through your bot to one chat, a group usually. Only sending: the bot doesn't read the chat, and nothing
you write there reaches claudesec.

It's a plugin (see the comment above `load_plugins` in `bin/claudesec`), off until you link it in. It adds:

| Hook | What |
|---|---|
| `commands` | `claudesec telegram send / check / chats` (below). The secretary may run `claudesec …` without a permission prompt, so sending never stops on one |
| `prompt` | a few lines in the secretary's system prompt: when the user asks to be sent something, how to use `send`. Workers' prompts are unchanged |

## Setting it up

1. In Telegram, send `/newbot` to [@BotFather](https://t.me/BotFather); keep the token it gives you (`123456:ABC…`).
2. Add the bot to the group that should get things, and send `/start@your_bot_username` there
   (with privacy mode on, which is the default, a bot in a group sees only commands addressed to it).
3. Link the plugin in:

       repo=$(dirname "$(dirname "$(readlink -f ~/.local/bin/claudesec)")")   # this checkout
       mkdir -p ~/.claudesec/plugins && ln -s "$repo/plugins/telegram" ~/.claudesec/plugins/telegram

4. Write the config yourself, readable only by you (claudesec refuses one others can read). Don't paste
   the token into a chat with the secretary: it would sit in the conversation's transcript.

       umask 077; cat > ~/.claudesec/telegram.json <<'EOF'
       {"bot_token": "123456:ABC…"}
       EOF
       claudesec telegram chats     # the chats the bot has seen in the last day: id, type, title

   Then add the group's id (`-100…`) as `"chat_id"`: `{"bot_token": "…", "chat_id": -100…}`.
5. Try it, and restart the secretary so its prompt has the new lines:

       claudesec telegram check     # bot @… → supergroup "…" (-100…): ready
       claudesec --restart

`~/.claudesec/telegram.json`:

| Field | |
|---|---|
| `bot_token` | from @BotFather. Never printed: not in output, errors or tracebacks |
| `chat_id` | where everything goes: a group's id (`-100…`), a user's (a number), or a public channel's `@name` |
| `proxy` | optional, e.g. `"http://127.0.0.1:7890"`, where api.telegram.org isn't reachable directly. Without it `https_proxy` from the environment applies. http(s) proxies only: for a socks one use its http port |
| `allow_private` | optional, directories, e.g. `["~/.claudesec/secrets/vpn-qr"]`: files only their owner may read (mode 600) are sent from these, from nowhere else. Credentials are refused even here; the home directory or above it is ignored |
| `api_base` | optional, default `https://api.telegram.org` (a local Bot API server, or the tests' fake one) |

## Commands

    claudesec telegram send /abs/shot.png /abs/report.pdf     # png / jpg / webp as photos, the rest as files
    claudesec telegram send --document /abs/qr.png            # pictures as files: no recompression
    claudesec telegram send -t 'one line' [FILE]              # text; with one file it goes as that file's caption
    claudesec telegram send -t - [FILE…] <<'EOF'              # long text, or text with quotes, from stdin
    …
    EOF
    claudesec telegram check                                  # the bot and the chat it sends to
    claudesec telegram chats                                  # chats the bot has seen, to find chat_id

Every file is checked first, and one bad path means nothing is sent: it must be there, not empty, at
most 50MB (the Bot API's limit), and not a secret. The secretary runs this without a permission prompt,
on paths a worker may have reported, so:

- **never sent**: `telegram.json` itself (under any name or link), anything in a credential place of the
  home directory (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`, `~/.kube`, `~/.docker`, `~/.password-store`,
  `~/.config/gh`, `~/.config/gcloud`, `~/.config/rclone`, browser profiles, `~/.netrc`, `~/.git-credentials`,
  `~/.npmrc`, `~/.pypirc`, … `CREDENTIAL_PATHS` in `plugin.py` has them all), files named like one
  (`id_rsa`, `id_ed25519`, `.env`, `.credentials.json`, `*.key`, `*.p12`, …), and any file with a private
  key or the bot's token in it. Checked on the path given and on the file it links to.
- **sent only from `allow_private`**: a file only its owner may read (mode 600 and the like). Put the
  directories of private files that are meant for you (VPN QR codes, say) in `allow_private` yourself.
  The secretary can't change that list without your say: `telegram.json` is outside what it may do
  unasked, and its prompt tells it not to.
- everything else, `~/.claudesec` included (reports, workers' output), goes.

There's no flag to override any of it. A file that's refused, you send from Telegram yourself.

Text over 4096 characters goes in up to three messages, cut between lines; longer than that, as one
`message.txt`. Text with two or more files goes first as a message of its own. A picture Telegram
won't take as a photo (`PHOTO_…` / `IMAGE_…` / dimensions errors) goes as a file. Telegram asking to slow
down (429) is waited out up to 60 seconds in all, then it gives up and says how long Telegram wants.
Each message is reported on stdout as it goes; the exit status is 1 on any failure, with Telegram's
reason on stderr.

## Tests

`tests/test_telegram.py` links this directory into a temporary `CLAUDESEC_HOME` and sends to a fake
Bot API on 127.0.0.1; nothing goes to Telegram. `python3 -m unittest discover -s tests`.
