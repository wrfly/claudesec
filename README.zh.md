# Claude Secretary

[English](README.md) | 中文

运行在 tmux 里的 Claude 秘书。你只跟主窗口说话；它把活派给 subagent 或独立 tmux 窗口里的 worker，
收汇报、做总结、只在需要你时打断你，并记住一切以便一个月后还能追溯。设计见 [DESIGN.md](DESIGN.md)。

## 安装（新机器从零）

需要：tmux、python3（3.8+）、Claude Code（`claude` 在 PATH 上，已经登录过一次）。

    git clone git@github.com:wrfly/claudesec.git && cd claudesec
    ./install.sh          # 软链 bin/claudesec 到 ~/.local/bin（确认它在 PATH 里）；不改任何 Claude Code 配置
    claudesec                  # 第一次运行建 ~/.claudesec/（config.json、queue.md），起 tmux session "claudesec"，里面是秘书

要英文界面的话，起之前先设语言（见[语言](#语言)）：

    mkdir -p ~/.claudesec && echo '{"language": "en"}' > ~/.claudesec/config.json

第一次在新目录里起 worker 时，Claude Code 可能先问「信任这个目录吗」，到那个窗口里答一次就好。

### 配置 `~/.claudesec/config.json`

第一次运行时用默认值生成，没写的字段用默认值；改完 `claudesec --restart` 生效。

| 字段 | 默认 | 说明 |
|---|---|---|
| `secretary_model` | `null` | 秘书用的模型；`null` 跟 Claude Code 的默认走。**建议写死**（如 `"claude-opus-5-5"`）：默认模型变成要额外额度的那种时，秘书启动会卡在确认框上 |
| `worker_model` | `"claude-opus-5-5"` | worker 默认模型，`claudesec spawn --model` 可单独指定 |
| `worker_permission_mode` | `"auto"` | worker 的 `--permission-mode` |
| `cursor_worker_model` | `"gpt-5.6-sol[context=1m,reasoning=high,fast=false]"`（GPT-5.6 Sol 1M High） | `spawn --agent cursor` 的默认模型（Cursor 的 `agent` CLI）。第一次开 Cursor worker 时会往 `~/.cursor/hooks.json` 加三条 hook（先备份），只在 claudesec 的 Cursor worker 里生效。本机要在 `~/.cursor/cli-config.json` 开 `"network": {"useHttp1ForAgent": true}`，否则 agent 走 HTTP/2 会卡住 |
| `runaway_pool_share` / `runaway_usd` / `runaway_quiet_minutes` | `0.3` / `20` / `60` | 没设额度的 worker 花得太多时告警：超过预算窗口上限的这个比例，或者上次汇报超过 M 分钟后又花了超过 $N |
| `cursor_max_running` | CPU 核数的一半 | 同时在干活的本地 Cursor worker 最多几个（停在提示符上空闲的不算；不配：本机核数的一半，至少 1；配了数字就按数字；`0` 不限）；超了 `spawn --agent cursor`、`wake`、`delegate` 会拒绝并说明是谁在跑。`0` 不限 |
| `cursor_nice` | `10` | Cursor agent 用 `nice -n` 这个值运行，CPU 让给别的程序；`0` 正常优先级 |
| `secretary_name` | `"secretary"` | 秘书的会话名，worker 用它发汇报 |
| `tmux_session` | `"claudesec"` | 秘书和 worker 所在的 tmux session |
| `cursor` | `"auto"` | Cursor worker（`spawn --agent cursor`、worker 的 `claudesec delegate`）能不能用。`"auto"`：机器上有 `agent` 命令就能用；`false` 关掉；`true` 要用（没装 `agent` 仍不可用）。不可用时这两个命令拒绝并说明原因，秘书和 worker 的规则里也不出现交给 Cursor 的内容。`claudesec status` 末尾显示当前状态。改完 `claudesec --restart`，worker 下次启动或唤醒时生效 |
| `dashboard` | `true` | 秘书窗口右侧的三块面板，从上到下：决策队列（待问、待答，搁置的折成最后一行）、任务进展（worker 的状态、距今、token 用量，搁置，未到点提醒）、通知消息（`claudesec notice` 登记的、要用户去做的事），3 秒刷新。`dashboard_side: "left"` 把这一列放到秘书左边。每条默认折叠成一行，点一条展开看全文，点标题栏全部展开 / 收起；各自用鼠标滚轮上下滚（焦点在面板上时也可以用 ↑↓ / PgUp PgDn / g G）。窗口宽 ≥110 列显示，更窄不显示；终端尺寸变了自动重排。`claudesec dashboard on|off` 切换 |
| `language` | 不设 | `"zh"` 或 `"en"`，见[语言](#语言) |
| `claude_config_dir` | 自动 | Claude Code 的配置目录。如果 `CLAUDE_CONFIG_DIR` 只在 claude 的启动脚本里设，从 tmux 起的 claudesec 进程看不到它；hook 第一次运行时会自动记到这里。想一开始就对，手动写上（比如 `"/home/me/.agentlodge/claude"`）|

### 语言

面板、`claudesec status`、`claudesec queue`，以及秘书和 worker 的提示词、告警、hook 注入，都有中文和英文两套。
在 `~/.claudesec/config.json` 里写 `"language": "en"`（或 `"zh"`），然后 `claudesec --restart`；新开或唤醒的 worker 自动跟着换。
不写时跟 Claude Code 自己 settings.json 里的 `language` 走：是英语就用英文，否则中文。不看系统 locale。

### 用 tmux-resurrect / continuum 的话

claudesec 的 session 不能被 resurrect 存下来，否则重启后它会被恢复成一堆裸的 `claude --resume`，没有 claudesec 的 prompt 和 hook，
还可能把秘书刚起的窗口顶掉。claudesec 给自己的 session 设了 `@resurrect-skip on`，保存钩子要据此把它从存档里去掉。
`contrib/resurrect-agent-commands.py` 就是这样一个钩子，同时会把其他 pane 里的 claude / codex / cursor agent
存成「恢复原来那段对话」的命令。tmux 配置里加：

    set -g @resurrect-processes 'claude agent codex cursor-agent'
    set -g @resurrect-hook-post-save-layout 'python3 /path/to/claudesec/contrib/resurrect-agent-commands.py'

已经有自己的保存钩子的，在里面跳过 `tmux list-sessions -F '#{session_name} #{@resurrect-skip}'` 里标了 `on` 的
session（它的 `pane`、`window` 行）即可。确认：`python3 contrib/resurrect-agent-commands.py --check`，
claudesec 的 pane 显示 `(not saved: @resurrect-skip)`。

### Shift+Enter 换行

`claudesec` 启动时会打开 tmux 的 `extended-keys`（并给 `xterm*` 终端加 `extkeys`），Shift+Enter 才能穿过 tmux 到达 Claude Code；
第一次启动后要断开 tmux 再接回来一次。外面跑 tmux 的终端还得能把 Shift+Enter 和 Enter 分开发：Ghostty、kitty、WezTerm、foot、
iTerm2 可以（Ghostty 不用配置）；GNOME Terminal 等 VTE 系的终端不行，tmux 怎么设都没用，在那里用 Alt+Enter 或 Ctrl+J 换行。

## 使用

    claudesec                  # 切到 tmux session "claudesec"（不存在就创建，恢复上次的秘书）
    claudesec --new            # 秘书从头开始（新会话）
    claudesec --restart        # 重新加载秘书的 prompt 和 hook，对话和 worker 都不受影响
    claudesec -d               # 只在后台起好，不切过去
    claudesec budget --until 20:00 --usd 100   # 给所有 worker 设一个花费窗口：剩 15% 提示收尾，到点冻结
    claudesec budget --add 50 | --until 23:00 | --from 09:00 --until 18:00 | --off   # 追加、预约下一个窗口、关掉
    claudesec cost --today      # 今天各 worker、各模型花了多少（按官方单价折算；claudesec cost 名字 --since 3h）
    claudesec dashboard off    # 关掉秘书右侧的队列 / 进展 / 通知面板（on 打开）
    claudesec notice add --from W "去 W 窗口说 push"   # 要用户去做的事；notice done N 标完成
    claudesec show 46          # 决策队列或通知 #46 的全文（面板里点一下那条也会展开）
    claudesec queue list --older 7   # 等了一周以上的未定条目
    claudesec queue doctor     # 检查 queue.md（行号前缀、重复分区、编号重复）；--fix 修
    claudesec model W M        # 把 worker W 换成模型 M（重开，对话保留）；Cursor worker 换模型就用它

队列面板上每条有个编号，直接回秘书「12 A」「15 不用了」就是在答那一条。

然后就在 secretary 窗口里说话。worker 会以同一个 session 里的 window 出现，名字就是任务名；
想看就切过去，不想看就不用管。原来的 tmux session 不受影响，随时切回去。

你偶尔需要做的：
- 秘书开窗口时会给一行收据，不对就说不要
- 它转错了，说「不是给 A 的，是给 B」
- 它一次只问你一件事；没空答的它会攒着
- 你每个上班日第一次找它时，它复盘一次（`claudesec review`）：整理它的记忆，把攒下的教训提成宪法 diff，作为一件事问你，你点头或摇头；想马上复盘就说「复盘」

退出秘书（`/exit`，或者直接关窗口）时，claudesec 会自动收起所有 worker，不留进程；下次 `claudesec` 会把它们恢复回来，
历史都在。重启电脑后也是跑一次 `claudesec`。想暂时不管某件事，跟秘书说「先放一边」，它会搁置起来；「接着弄 X」再唤醒。

已经在跑的 Claude 会话也能交给秘书：跟它说「接管 blog」或「等 blog 停了接管它」。
接管会在原窗口重启那个会话，对话历史保留。

## 带到别的机器

repo 里的是对谁都适用的部分：`prompts/secretary.md`（秘书宪法，含复盘后定下的约定）、`prompts/worker.md`，以及它们在 `prompts/en/` 里的英文版。
`~/.claudesec/` 是这台机器的状态（注册表、历史、教训、决策队列），秘书的 Claude Code memory 是这台机器上的偏好，都不在 repo 里；
要连历史一起搬，复制 `~/.claudesec/history/` 即可（任务文件里的 session id 只在原机器上能 `--resume`）。
