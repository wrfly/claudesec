# Aidecamp

Your AI aide-de-camp for long-running work.

终端里的 AI 机要副官。你只跟主窗口说话；它把活派给 subagent 或独立 tmux 窗口里的 worker，
收汇报、做总结、只在需要你时打断你，并记住一切以便一个月后还能追溯。设计见 [DESIGN.md](DESIGN.md)。

## 安装（新机器从零）

需要：tmux、python3（3.8+）、Claude Code（`claude` 在 PATH 上，已经登录过一次）。

    git clone git@github.com:wrfly/aidecamp.git && cd aidecamp
    ./install.sh          # 软链 bin/aidecamp 到 ~/.local/bin（确认它在 PATH 里）；不改任何 Claude Code 配置
    aidecamp                  # 第一次运行建 ~/.aidecamp/（config.json、queue.md），起 tmux session "aidecamp"，里面是秘书

第一次在新目录里起 worker 时，Claude Code 可能先问「信任这个目录吗」，到那个窗口里答一次就好。

### 配置 `~/.aidecamp/config.json`

第一次运行时用默认值生成；改完 `aidecamp --restart` 生效。

| 字段 | 默认 | 说明 |
|---|---|---|
| `secretary_model` | `null` | 秘书用的模型；`null` 跟 Claude Code 的默认走。**建议写死**（如 `"claude-opus-5-5"`）：默认模型变成要额外额度的那种时，秘书启动会卡在确认框上 |
| `worker_model` | `"claude-opus-5-5"` | worker 默认模型，`aidecamp spawn --model` 可单独指定 |
| `worker_permission_mode` | `"auto"` | worker 的 `--permission-mode` |
| `secretary_name` | `"secretary"` | 秘书的会话名，worker 用它发汇报 |
| `tmux_session` | `"aidecamp"` | 秘书和 worker 所在的 tmux session |
| `claude_config_dir` | 自动 | Claude Code 的配置目录。如果 `CLAUDE_CONFIG_DIR` 只在 claude 的启动脚本里设，从 tmux 起的 aidecamp 进程看不到它；hook 第一次运行时会自动记到这里。想一开始就对，手动写上（比如 `"/home/me/.agentlodge/claude"`）|

### 用 tmux-resurrect / continuum 的话

aidecamp 的 session 不能被 resurrect 存下来，否则重启后它会被恢复成一堆裸的 `claude --resume`，没有 aidecamp 的 prompt 和 hook，
还可能把秘书刚起的窗口顶掉。aidecamp 给自己的 session 设了 `@resurrect-skip on`，保存钩子要据此把它从存档里去掉。
`contrib/resurrect-agent-commands.py` 就是这样一个钩子，同时会把其他 pane 里的 claude / codex / cursor agent
存成「恢复原来那段对话」的命令。tmux 配置里加：

    set -g @resurrect-processes 'claude agent codex cursor-agent'
    set -g @resurrect-hook-post-save-layout 'python3 /path/to/aidecamp/contrib/resurrect-agent-commands.py'

已经有自己的保存钩子的，在里面跳过 `tmux list-sessions -F '#{session_name} #{@resurrect-skip}'` 里标了 `on` 的
session（它的 `pane`、`window` 行）即可。确认：`python3 contrib/resurrect-agent-commands.py --check`，
aidecamp 的 pane 显示 `(not saved: @resurrect-skip)`。

## 使用

    aidecamp                  # 切到 tmux session "aidecamp"（不存在就创建，恢复上次的秘书）
    aidecamp --new            # 秘书从头开始（新会话）
    aidecamp --restart        # 重新加载秘书的 prompt 和 hook，对话和 worker 都不受影响
    aidecamp -d               # 只在后台起好，不切过去

然后就在 secretary 窗口里说话。worker 会以同一个 session 里的 window 出现，名字就是任务名；
想看就切过去，不想看就不用管。原来的 tmux session 不受影响，随时切回去。

你偶尔需要做的：
- 秘书开窗口时会给一行收据，不对就说不要
- 它转错了，说「不是给 A 的，是给 B」
- 它一次只问你一件事；没空答的它会攒着
- 说「复盘」，它把攒下的教训交给 `aidecamp` worker 改宪法，你对 diff 点头或摇头

退出秘书（`/exit`，或者直接关窗口）时，aidecamp 会自动收起所有 worker，不留进程；下次 `aidecamp` 会把它们恢复回来，
历史都在。重启电脑后也是跑一次 `aidecamp`。想暂时不管某件事，跟秘书说「先放一边」，它会搁置起来；「接着弄 X」再唤醒。

已经在跑的 Claude 会话也能交给秘书：跟它说「接管 blog」或「等 blog 停了接管它」。
接管会在原窗口重启那个会话，对话历史保留。

## 带到别的机器

repo 里的是对谁都适用的部分：`prompts/secretary.md`（秘书宪法，含复盘后定下的约定）、`prompts/worker.md`。
`~/.aidecamp/` 是这台机器的状态（注册表、历史、教训、决策队列），秘书的 Claude Code memory 是这台机器上的偏好，都不在 repo 里；
要连历史一起搬，复制 `~/.aidecamp/history/` 即可（任务文件里的 session id 只在原机器上能 `--resume`）。
