# cmux

tmux 里的 Claude 秘书。你只跟主窗口说话；它把活派给 subagent 或独立 tmux 窗口里的 worker，
收汇报、做总结、只在需要你时打断你，并记住一切以便一个月后还能追溯。设计见 [DESIGN.md](DESIGN.md)。

## 安装

    ./install.sh          # 软链 bin/cmux 到 ~/.local/bin，不改任何 Claude Code 配置

## 使用

    cmux                  # 切到独立的 tmux session "cmux"，里面是秘书（不存在就创建，恢复上次的秘书）
    cmux --new            # 秘书从头开始（新会话）
    cmux -d               # 只在后台起好，不切过去

然后就在 secretary 窗口里说话。worker 会以同一个 session 里的 window 出现，名字就是任务名；
想看就切过去，不想看就不用管。原来的 tmux session 不受影响，随时切回去。

你偶尔需要做的：
- 秘书开窗口时会给一行收据，不对就说不要
- 它转错了，说「不是给 A 的，是给 B」
- 说「复盘」，对它提出的自我修改点头或摇头

已经在跑的 Claude 会话也能交给秘书：跟它说「接管 xixitv」或「等 61av 停了接管它」。
接管会在原窗口重启那个会话，对话历史保留。

配置在 `~/.cmux/config.json`（worker 默认模型、权限模式、秘书名、秘书模型）。
