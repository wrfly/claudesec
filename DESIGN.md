# ClaudeSec 设计文档 —— tmux 里的 Claude 秘书（最小版）

> 状态：设计稿 v2，未实现。给执行者（另一个 Claude Code 会话）看的。不含代码。
> v1 有部门、审核门、三档模型分级、三层结构——都砍了，原因见第 1 节。

---

## 0. 一句话

用户只跟一个 Claude Code 会话（**秘书**）说话。秘书默认用进程内 subagent 派活；
只有任务跑得久、要中途插话、要活过秘书重启时，才升级成一个 tmux 窗口里的独立会话
（**worker**）。worker 用固定格式汇报，秘书只在该打断时打断用户，用户随时能问进展。

---

## 1. 原则 —— 为什么是这个形状

### 1.1 唯一的分层理由：上下文窗口有限

模型足够聪明，一个会话能从调研做到实现到测试。流水线（规划→审核→派发→执行）是
给技能窄、会偷懒、需要制衡的人类组织设计的，对模型只增加延迟和信息损耗。

分层的唯一正当理由：**某件事的中间细节会把另一件事挤出上下文**。所以：

> 层数 = 让每个上下文窗口都不爆的最小值。按上下文边界分，不按技能分。

由此推出：
- 秘书不是新东西，就是用户已经在用的主会话。它的上下文 = 用户注意力的代理，
  进去的每个 token 都得是高信号的。
- 不设部门（按技能分是人的习惯）。任务结束，上下文即弃。
- 不设审核门。
- 不预设模型分级。先一个模型跑；账单疼了再加一条规则。

### 1.2 两级派活，升级有门槛

| 级别 | 机制 | 什么时候用 |
|---|---|---|
| **subagent** | 秘书会话内的 `Agent` 工具；并行；返回值即汇报；`fork` 可带上下文 | **默认**。绝大多数任务 |
| **tmux worker** | 独立 Claude Code 会话，一个 tmux window | 满足任一：① 预计跑很久且用户要中途插话 ② 必须活过秘书重启/compact ③ 用户想亲眼盯 ④ 输出量大到返回值塞不下 |
| **讨论窗口** | tmux worker 的讨论模式（`spawn --discuss`） | 用户要来回讨论而非交付物。一律开窗口，用户被切过去直接对话；讨论中不逼汇报；结束时 worker 发 DONE（结论 + 取舍 + TODO），hook 把用户切回秘书，秘书总结并问要派哪些待办 |

升级是秘书的判断，但要给用户一行收据（「开了窗口 `auth`：重构 token 校验」），
用户看一眼就能否决。拿不准 → 用 subagent。subagent 不够再升级，代价只是重做一次。

worker 自己可以再开 subagent，那是它的事；subagent 结果只回到 worker，
worker 消化后再汇报。**秘书永远不直接看 subagent 输出。**

### 1.3 非目标

- 不做跨机器、不做 web UI、不做 swarm。
- 不替代 git worktree / 分支策略。
- worker 之间不直接聊，有需要经秘书。
- v1 不写二进制。先用 Claude Code 自带能力 + prompt + settings。

---

## 2. 行为 —— 秘书和 worker 各自怎么做

### 2.1 用户发言分类

秘书收到一句话先分类再动：

| 模式 | 识别信号 | 动作 |
|---|---|---|
| **派单** | 祈使句，跟在跑的事无关 | 判 subagent 还是 worker（1.2）→ 派 → 收据 |
| **转发** | 对某个在跑的 worker 的补充/修正/追问 | 判目标（2.2）→ **原话**转发 → 「已转给 X」 |
| **查询** | 「进展」「怎么样了」「谁等我」 | 从注册表渲染状态表（2.4），不打扰 worker |
| **决策** | 回答某个 worker 之前的 `NEED_DECISION` | 带原问题转发回去 |
| **控制** | 「停掉 X」「X 换思路」 | 停 / 重定向 / 带交接摘要重开 |
| **元指令** | 「别打扰我」「有事立刻说」 | 改秘书运行参数，持久化 |

分类拿不准时按代价排序：问一句 < 转错（可撤） < 开错 worker。
派单 vs 转发模糊 → 倾向转发，先问「是给 X 的补充还是新任务？」

只有 worker 存在时才有转发/决策/控制；只用 subagent 时秘书就是普通会话。

### 2.2 路由：这句话是对哪个 worker 说的

自上而下取第一个命中：
1. 显式点名：`@auth`、「让 auth 那个…」
2. 话题匹配：跟某个 worker 的任务摘要 / 最近汇报重合
3. 焦点延续：默认 = 上一个被转发或刚汇报过的 worker。秘书维护显式 `focus` 字段
4. 问一句，不解释

纠错必须便宜：「不是给 A 的，是给 B」→ 转给 B，同时告诉 A「上一条忽略」。

用户直接点进 worker 的窗口说话是允许的；worker 的下一条汇报要注明，秘书据此更新。

### 2.3 worker → 秘书：汇报协议

```
TYPE: PROGRESS | BLOCKED | NEED_DECISION | DONE | FAILED
DID:  一两句
RESULT: 改了哪些文件 / 结论 / 测试结果      (DONE 必填)
ISSUE: 问题                                 (BLOCKED/FAILED 必填)
ASK:  需要用户回答的问题 + 选项 ≤ 4           (NEED_DECISION 必填)
NEXT: 接下来做什么                           (PROGRESS 必填)
DECISIONS: 自己做的取舍，每条「选了 X 而非 Y，因为 Z」  (DONE/FAILED 必填，可为「无」)
```

`DECISIONS` 只为溯源，秘书不推送、不总结，原样进历史库（3.5）。

时机：动手前一条 PROGRESS（含计划，让秘书能早拦）；每个有意义的阶段一条；
阻塞/需决策立刻；结束 DONE/FAILED；用户直接跟它说过话则注明。

兜底：`TeammateIdle` 或等价 hook 检查本轮有无 DONE/FAILED，没有则逼它补一条。

### 2.4 秘书 → 用户：推送与查询

**推送**只在 `BLOCKED` `NEED_DECISION` `DONE` `FAILED`。`PROGRESS` 静默落盘。
多条同时到 → 合并成一条。格式：`[worker] 类型：一句话`。
元指令可调：「别打扰我」全攒着；「X 的一切都告诉我」单个全推。

**查询**一屏以内：

```
worker   状态      最近                         等你？
auth     进行中    改完校验逻辑，跑测试中
perf     需要决策  两种 cache 方案，等你选        ← 是
```

「X 做到哪了」→ X 近三条汇报原文。「X 细节」→ 秘书读 X 的落盘日志再答。

秘书把「用户决策：…」转给一个 NEED_DECISION / BLOCKED 的 worker 时（SendMessage 的 hook，或 Cursor 的 `send`），
注册表里它就改成 PROGRESS、摘要「已答，在处理：…」，「等你？」随之消失，不用等它下一次汇报。权限对话框不算，那要在它窗口里点。
更一般地：worker 的最后一条汇报（DONE / FAILED / BLOCKED / NEED_DECISION）之后，它只要开始了新的一轮，也改回 PROGRESS
（摘要「收到回复，在处理：…」或「在做新的一轮（上次 DONE：…）」），直到下一次汇报。判据：汇报时写 `workers/<名>/after-report`，
那一轮结束（Stop hook）把它标成 idle；Claude worker 之后第一次非 SendMessage 的工具调用（PostToolUse `*` 的守卫检查这个文件，
平时不起进程），Cursor worker 下一次提交提示（beforeSubmitPrompt），就改回在做。汇报那一轮里的收尾调用不算。

### 2.5 秘书的铁律

- **不自己干项目活**。判断：要读/改项目文件、要跑命令？→ 派出去。
  例外是即时问答（「刚才 A 说什么」）。
- **转发不改写**。最多加一行上下文（「这是回答你的 NEED_DECISION」）。
- **总结不添加**。只压缩 worker 说过的；拿不准标「worker 未说明」。
- **升级给收据**。

---

## 3. 结构 —— 由什么组成

### 3.1 底座

subagent 级：Claude Code 自带，零配置。

worker 级两个选项，协议（2.3）不绑定传输：

| | A. Agent Teams（tmux 模式） | B. 独立会话 + cross-session messaging |
|---|---|---|
| 开窗口 | 自动，但是 lead 窗口内**分 pane** | 秘书自己 `tmux new-window` + `claude …`，是独立 **window** |
| 汇报到达 | 消息 + idle notification 自动带最终答案 | worker `SendMessage` 到秘书会话名；`notify_when_idle` 一次性 |
| 限制 | 一会话一 team；resume 后协调状态丢；worker 权限提示冒泡到秘书 | 全手动，没有 team 概念 |
| 恢复 | 弱 | 强：各会话独立 `--resume` |

**选 B。** 理由：用户要 window 不要 pane；worker 本来就是「要活过秘书」的东西，
B 的独立性正好。秘书多做的事只是拼 tmux 命令和维护注册表，而注册表本来就要有。
已验证：本机 Claude Code 2.1.301 的 `ListAgents` 能看到其他会话的名字、busy/idle、
tmux pane id，`SendMessage` 可互发。

实测结果（2026-10-16，claude 2.1.301）：
1. 新 window 里的会话约 30 秒内出现在 `ListAgents`，带 tmux pane id。
2. **秘书空闲时，worker 的 `SendMessage` 能直接唤醒它**。汇报无需走文件 + Monitor。
3. 会话的自动名每次重启都变（`claudesec-48` → `claudesec-c4` → `claudesec-5e`）→ 秘书必须用固定 `-n` 启动。
4. 新会话可能弹「模型额度」对话框卡住无人看的窗口，且在里面选「切换」会改全局默认模型
   → spawn 一律显式 `--model`，并在 spawn 后 8 秒内抓 pane 检测对话框。
5. `--agent` 的正文会**替换**默认系统提示 → 不用 agent 文件，改用 `--append-system-prompt-file`。
6. worker 等后台 subagent 时会结束本轮；Stop hook 若照常逼汇报，worker 只能发假 BLOCKED
   → Stop hook 输入里 `background_tasks` 非空时放行。

### 3.2 组件（已实现）

```
<repo>/bin/claudesec            python3 单文件：秘书用的命令 + 全部 hook 逻辑
<repo>/prompts/secretary.md  秘书宪法（--append-system-prompt-file 追加到默认系统提示）
<repo>/prompts/worker.md     worker 规则，{{NAME}} {{SECRETARY}} 在 spawn 时替换
<repo>/install.sh            只做一件事：软链 bin/claudesec 到 ~/.local/bin
~/.claudesec/                     状态目录（CLAUDESEC_HOME 可改）
  config.json                秘书名、秘书模型、worker 默认模型、worker 权限模式
  registry.json              注册表（3.3），hook 写，加文件锁
  reports/<worker>.log       双向全部消息：task / worker→secretary / secretary→worker
  workers/<name>/            每个 worker 的 prompt.md、task.md、settings.json（hooks、autoMemoryDirectory）
  history/                   3.5
  usage.json                 token 计数的缓存：每个 transcript 读到哪、累计多少（见「主窗口面板」）
  lessons.md                 3.6
  queue.md                   决策队列：要用户定的事按重要程度排队，秘书一次只问一件（首次启动时建）。
                             每条 `- #编号 [worker] 问题（中）`；编号由 `claudesec queue add` 给，跟着条目走
  queue.last                 最后发出去的队列编号：「已处理」清掉了也不会再发同一个号
  secretary.session          秘书 session id，`claudesec` 据此 --resume
  secretary-settings.json    秘书的 hooks；关掉输入框的 Tab 补全预测（promptSuggestionEnabled: false）；autoMemoryDirectory
```

hook 全部通过 `--settings` 挂在单个会话上，**不碰用户的全局 settings.json**：

| 会话 | 事件 | 做什么 |
|---|---|---|
| worker | SessionStart | 把 session id 写进 registry（溯源第三层） |
| worker | PostToolUse(SendMessage) | 发给秘书的消息 → 追加 log、解析 TYPE、更新 status/summary/pending_ask/focus；关键类型写「已汇报」标记 |
| worker | Stop | 有后台任务 → 放行；有标记 → 清标记放行；`stop_hook_active` → 放行；否则 block 要求汇报 |
| 秘书 | SessionStart（含 compact/resume） | 记 session id；注入状态表 + INDEX 最近 20 行 + 待复盘教训数 |
| 秘书 | PostToolUse(SendMessage) | 发给 worker 的消息 → 追加到该 worker 的 log、更新 focus |
| worker | PermissionRequest / Notification(permission_prompt 等) | 写「等待确认」标记；4 秒后仍未处理（排除自动放行的调用）→ 状态 WAITING_PERMISSION、记 log、写告警到 `~/.claudesec/alerts/`（讨论窗口不告警，用户就在窗口里） |
| worker | PostToolUse / PostToolUseFailure / PermissionDenied（全部工具）、Stop、汇报 | 有标记才进 python：清标记，状态恢复 |
| worker | StopFailure（这一轮死在 API 错误上，如代理的 524） | 状态 API_ERROR（显示「API 出错」）、记 log、写告警叫醒秘书，秘书让它从中断处继续；下一轮正常结束时状态恢复（讨论窗口不告警） |
| 秘书 | SessionStart、Stop，`asyncRewake` | 单实例 watcher（flock）在后台等告警；有告警就以 exit 2 退出 → **叫醒秘书**并送达内容；每轮结束重新挂上；秘书进程退出时 watcher 自行退出 |

**memory 不弹框**：Claude Code 本来就不为会话自己的 auto memory 目录弹确认框，例外是这个路径经符号链接落到受保护的地方。
这台机器上 `~/.agentlodge/claude/projects` 链到 `~/.claude/projects`：豁免按请求的路径算，保护按解析后的路径算（`~/.claude` 下），
于是每次写 memory 都被当成敏感文件。allow 规则救不了：受保护路径不认 allow 规则。所以 settings 里写
`autoMemoryDirectory` = 解析后的真实目录，同一批文件，路上不经过链接。只在确实有链接时写；算不准的情况不写，照 Claude Code
默认走：裸仓库、submodule，以及目录名超过 200 字符（Claude Code 会加哈希缩短）。目录按 git 主 checkout 定，worktree 和子目录
共用一个；不在 git 里就按 cwd。
验证（`claude -p`，haiku，临时目录）：经链接的路径，default 和 auto 两种模式写入都被拒，加了 allow 规则也一样被拒；
设成解析后的目录，不加任何规则，default、auto 两种模式写、default 模式读都直接通过。worktree 和子目录在 init 事件里
报出的 memory 路径都是主仓库的。worker 改 memory 用 Write / Edit（worker.md）；Bash 的 `rm`、`cat >` 照样会问。

**Cursor 的错误和换模型**：Cursor 的 stop hook 只给 status=error，不给原因；原因在屏幕上（2026-10-25 upreset 被切到被团队屏蔽的
Grok 4.7，几个小时每轮都「Error: Model Blocked」，告警只写了 status error）。现在出错时截一下它的窗口，取最后一行带 Error /
Blocked 的字，连同 hook 给的模型名写进 pending_ask、日志和告警；像是模型用不了时告警直接给 `claudesec model`。
Cursor 的模型只认命令行上的 `--model`，往窗口里贴 `/model …` 只会变成一条发给模型的消息：`claudesec send` 拒绝以 `/model` 开头的
内容并指向 `claudesec model <名字> <模型>`，它就是 shelve（忙的不动，`--force` 才打断）再 quiet wake `--model`，对话保留，
Claude Code worker 也能用。

**本地 Cursor 的 CPU**：每个 agent 是一棵本地进程树，能吃满几个核（用户嫌占 CPU，2026-10-24）。上 Cursor 云端
（Cloud Agents，只有 REST API 能脚本化，要关掉账号的 privacy mode、给仓库装 GitHub App、云端命令全自动执行）用户没选，
选了在本地压着：config `cursor_max_running`（不配时按本机 CPU 核数的一半，至少 1；配数字按数字，0 不限；10-26 前默认是 2）个以上的 Cursor worker 在干活（STARTING / PROGRESS、窗口还在）时，
`spawn --agent cursor`、`wake`、`delegate` 拒绝并列出谁在跑，delegate 的拒绝带 `NOT DELEGATED:`，worker 照规则自己做或开
subagent；接着做同一个 worker 不算新增。停在提示符上的不算（几乎不占 CPU）。`cursor_nice`（默认 10）：agent 命令前加
`nice -n`，整棵进程树继承。`claudesec status` 的 Cursor 那行显示「在跑 N/上限」。

**生命周期**：秘书窗口里跑的是 `claudesec run`，它不 exec claude，而是把它当子进程包一层。
- 启动时：后台 `claudesec lifecycle wake-all`。被秘书退出收起的 worker（`shelved_by: exit`），以及进程已经没了的
  worker（窗口或整个 tmux session 被杀），都用 `--resume` **静默**恢复：不带首条 prompt，不跑模型，状态还原。
  用户手动搁置的不动。恢复失败的写告警，叫醒秘书。
- 退出时（`/exit`、窗口被关、tmux session 被杀，包装进程都忽略 SIGHUP 并继续收尾）：后台 `claudesec lifecycle shelve-all`
  收起所有 worker；忙着的等它做完这一轮；秘书在此期间回来了就停手。
- `claudesec --restart` / `--new` 先写 `restarting` 标记，退出的包装进程看到标记就不收尾，worker 照常跑。
- wake-all 和 shelve-all 靠 `lifecycle.lock` 串行。秘书可能启动几秒就退出（比如开机时被 tmux-resurrect 顶掉），
  这时先把 worker 唤醒完，再收起来。正在唤醒的 worker 在注册表里没有 session，两边同时动手会互相踩。
  `run.pid` 记着当前的 `claudesec run`：旧秘书的 shelve-all 看到有新的就停手；wake-all 发现启动它的秘书已经不在，就什么也不唤醒。
- 开机：claudesec 的 tmux session **不让 tmux-resurrect 保存**。claudesec 给它设了 `@resurrect-skip on`，resurrect 的
  post-save-layout 钩子要把带这个选项的 session 从存档里去掉。否则恢复出来的 pane 是裸的 `claude --resume`，
  没有 claudesec 的 prompt、hook 和包装层；而且如果 tmux 服务器是 `claudesec` 自己拉起的，continuum 一恢复就会把秘书的
  pane 当作「从零恢复」顶掉。去掉之后，开机后跑 `claudesec`：秘书由 claudesec 拉起，worker 由 wake-all 恢复。
- `claudesec` 发现 secretary 窗口里跑的不是 `claudesec run`（一个 shell，或者恢复出来的裸 `claude --resume`），就接管：
  用 `claudesec run` 重开这个 pane，对话照样 `--resume`。
- 这些都是 claudesec 自己执行的代码，不是秘书的工具调用，所以不会被 auto mode 分类器拦，用户也不用做杂活。
- 秘书不在时，worker 的 Stop hook 不再逼汇报；SendMessage 失败的汇报也照样记进 log 和 registry（PostToolUseFailure）。
- `CLAUDE_CONFIG_DIR` 可能只在 claude 的启动脚本里设，tmux 起的 claudesec 进程看不到。hook 运行在 claude 之下，
  会把它记进 `config.json` 的 `claude_config_dir`，其他 claudesec 进程从这里读。

结果：秘书**不记账**。日志、注册表、焦点都由 hook 确定性维护，秘书只负责判断和措辞。

worker 卡在确认框上时自己发不了 `SendMessage`，hook 是 shell 也发不了，Claude Code 又没有发跨会话消息的 CLI，
所以告警走「文件 + 秘书侧 asyncRewake watcher」。已实测：空闲的秘书会被叫醒并收到告警。

### 3.3 注册表（秘书的外脑）

```json
{
  "focus": "perf",
  "workers": {
    "auth": {
      "session": "claudesec-auth-12",
      "window": "0:@7",
      "task": "重构 token 校验，加过期处理",
      "status": "PROGRESS",
      "last_report_at": "2026-10-16T22:10:03+08:00",
      "last_summary": "改完校验逻辑，跑测试中",
      "pending_ask": null,
      "owns_files": ["internal/auth/**"]
    }
  }
}
```

落盘而非只放上下文：秘书上下文要留给用户；compact 之后只有磁盘可靠。
`owns_files`：派新任务若触及已占路径，先问用户是合并给原 worker 还是等。

### 3.5 记忆：三种时效，三个地方

| 时效 | 内容 | 存在哪 | 谁写 | 何时进上下文 |
|---|---|---|---|---|
| 小时级 | 谁在跑、到哪了 | `registry.json`（3.3） | 秘书，每次汇报后 | 常驻摘要 |
| 天～月级 | 做过什么、结果、改动文件、**决定及理由** | `~/.claudesec/history/`（下） | 秘书，任务收尾时 | 只有 `INDEX.md` 常驻；正文按需检索 |
| 永久 | 用户偏好、常设指令 | Claude Code 自带 memory + `settings.json` | 秘书，用户给元指令时 | 自带机制负责 |

**历史库**，形状跟 Claude Code 自己的 memory 一样：一事一文件 + 一个索引。

```
~/.claudesec/history/
  INDEX.md                      一行一任务：日期 · 项目 · 名字 · 一句话结果 · 标签
  2026-10/
    auth-token-refactor.md
    perf-cache-investigation.md
```

任务文件结构：

```markdown
---
date: 2026-10-16
project: ~/Documents/github/foo
worker: auth              # 或 subagent
session: 7f3a…            # worker 的 Claude Code session id；`claude --resume 7f3a…` 可看完整 transcript
status: DONE
files: [internal/auth/token.go, internal/auth/token_test.go]
tags: [auth, refactor]
---
## 任务
用户原话

## 用户决策
- 2026-10-16 15:02 过期时间改成可配置 —— 用户选了 env var 而非 config 文件，理由：部署方便

## 结果
DONE 汇报的 RESULT 原文

## worker 决策
DONE 汇报的 DECISIONS 原文，每条「选了 X 而非 Y，因为 Z」

## 过程
reports/auth.log 全文（PROGRESS 也在）
```

溯源分三层，由浅入深，平时只碰第一层：
1. `INDEX.md` 一行 → 2. 任务文件（用户决策 / worker 决策 / 过程）→
3. `claude --resume <session>` 看 worker 完整 transcript（每个工具调用、每次改动都在）。
第三层零成本：Claude Code 本来就存 transcript，秘书只需记下 session id。
subagent 完成的任务没有第三层，所以 subagent 的返回值要秘书原样存进「过程」。

规则：
- **只在收尾写**：DONE/FAILED 时由秘书从 registry + log 生成；subagent 完成的任务也写
  （秘书有返回值，够写）。NEED_DECISION 被用户回答时追加到「决策」节——这是最高价值的记忆，
  一个月后被问的多半是「当时为什么这么选」而不是「改了哪几行」。
- **worker 不写历史，但要在 DONE/FAILED 里交出 `DECISIONS`**。它不知道什么跨任务重要，
  但只有它知道自己放弃了哪些方案。秘书不过滤，原样存；INDEX 和推送里不出现。
- **分类靠标签 + 按月目录**，不预设分类树。标签由秘书收尾时打，用户可在元指令里规定标签集。
- **检索走 subagent**：用户问旧事 → 秘书派 Explore 在 `~/.claudesec/history/` 按关键词/日期/
  标签 grep INDEX 再读命中文件 → 只把结论带回。秘书永远不把历史正文整段读进自己上下文。
- **秘书会话长期 resume**，不每天新开。registry + history 在磁盘上，compact 才安全。
  `SessionStart` hook 注入 registry 和 INDEX 最近 20 行。

### 3.6 进化：秘书自己改自己，分两层

秘书手里有最好的训练数据：用户的每次纠错都是带标签的样本。信号来源：
- 路由纠错：「不是给 A 的，是给 B」
- 否决收据：「这个不用开窗口」「这个该开窗口」
- 查询不够用：问完「进展」又追问细节 → 状态表粒度不对
- 元指令：「以后 X 类的都…」
- 推送被嫌烦 / 该推没推

| 层 | 内容 | 机制 | 审批 |
|---|---|---|---|
| **经验** | 事实、偏好、别名（「用户说 perf 指性能那个 worker」） | Claude Code 自带 memory：纠错即写一条 feedback memory，下次会话自动加载 | 不需要 |
| **宪法** | `secretary.md` 的规则 | 秘书在 `~/.claudesec/lessons.md` 攒教训，不自己改宪法；每天一次（周期提醒到点，或用户说「复盘」）跑 `claudesec review`，开一个当天的复盘 worker 归类、提 diff（同时提出删什么）；用户点头后落盘，下次启动生效。对谁都适用的约定进宪法跟着 repo 走，机器/项目专属的留在 memory | 用户看 diff |
| **铁律** | 2.5 节四条 | 永不自改 | — |

为什么宪法层要门槛和审批：
- 漂移：规则只加不减会撑爆秘书自己的上下文——正是最防的事。所以提 diff 时必须同时提议删掉什么
- 过拟合：纠正一次就写「从此所有 X 都给 B」
- 没有回归测试：改坏了自己没人发现

`lessons.md` 格式：日期 · 信号类型 · 当时情况一句话 · 用户纠正原话。
每日复盘（`claudesec review`，任务书在 prompts/review.md）：
- 触发：用户当天第一次给秘书发消息时。秘书的 UserPromptSubmit hook 把日期记在 `~/.claudesec/user-seen.date`，
  日期变了就给这一条注入「今天第一次见用户：先跑 claudesec review」。跟着用户出现走，不跟时钟：
  周末、请假没人发消息就不复盘，隔几天回来也只跑一次（用户定的，原先的每日 08:30 定时提醒因此撤掉）。
  跨会话消息、后台任务通知、claudesec 告警不算用户的消息。用户说「复盘」时随时跑。
- 每天开一个新 worker（`review-MMDD`，在 claudesec 仓库目录），不 wake 长期的 `claudesec` worker：
  复盘用不到昨天的上下文，长期 worker 会把每天的复盘都背在上下文里。
- 记忆（经验层）直接整理落盘。`claudesec review` 派 worker 前自己把目录拷到 `~/.claudesec/reviews/<日期>-memory-before/`，
  并把秘书的 memory 目录设成这个 worker 的 auto memory 目录（`autoMemoryDirectory`），改记忆不弹框（见下「memory 不弹框」）。
- 宪法层只出 diff：分支 `review/<日期>` 不 push，`git diff origin/main` 存到 `~/.claudesec/reviews/<日期>.diff`，
  NEED_DECISION 一行一处改动，进秘书的决策队列；用户决定后 worker 自己 rebase、push，把教训移到「已处理」。

### 3.4 数据流

```
用户发言
  ▼
秘书分类 ─派单─► subagent（默认）────────────► 返回值 → 总结给用户
         │       或 tmux worker（达到门槛）─► registry，收据
         ├转发─► 路由 → SendMessage(原话) → 「已转给 X」
         ├查询─► 读 registry/log → 状态表
         ├决策─► pending_ask 的 worker
         ├控制─► 停 / 带交接摘要重开
         └元指令► settings.json
worker 干活（可开 subagent，结果不外泄）
  ▼ 汇报(2.3)
秘书：log → registry → focus → 按阈值推送
  ▼
用户只看到合并后的一行 / 一表
```

---

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| 秘书自己干活 | 铁律；用户一句「派出去」纠正 |
| 秘书上下文爆 | registry + log 落盘；PROGRESS 不进上下文 |
| 过度升级成 worker（窗口泛滥） | 门槛（1.2）；拿不准用 subagent；收据让用户否决 |
| worker 悄悄结束不汇报 | hook 兜底；秘书 `notify_when_idle` 第二保险 |
| worker 停在 API 错误上不汇报 | StopFailure hook 告警，秘书叫它继续 |
| 两 worker 改同一文件 | `owns_files`；需要时 worktree |
| 路由错 | 一句话可撤；focus 显式可见 |
| 秘书 resume/compact 后状态丢 | registry 是唯一真相；对不上的 window 标 `orphan` 让用户决定 |
| 用户直接进窗口说话 | worker 下条汇报注明；秘书不假装全知 |
| 问一个月前的事，秘书答不上或编 | 历史库 + 决策单独记录；检索走 subagent；查无此事就说「没有记录」 |
| 历史库越写越大、INDEX 太长 | 按月分目录；SessionStart 只注入 INDEX 最近 20 行；检索靠 grep 不靠通读 |

---

## 5. 验收

1. 一次会话里 ≥3 个任务并行，用户全程不离开主窗口。
2. 其中至少一个走 subagent、一个走 tmux worker，且 worker 内部开过 subagent，
   秘书收到的只是消化后的汇报。
3. 任意时刻问「进展」，一屏以内，含谁在等用户。
4. 秘书全程没读/改过项目文件（transcript 可核对）。
5. 秘书 `/compact` 一次后状态表仍正确。

## 6. 留给用户决定的

- 秘书和 worker 的模型（默认同一个）
- 推送阈值（默认 BLOCKED / NEED_DECISION / DONE / FAILED）
- worker 是否强制 worktree（默认否）
- worker 权限模式（默认继承；建议预批 read/test 类）

## 7. 以后再说

- 模型分级：账单疼了再加，一条规则「机械活用 haiku」起步
- Go 二进制 `claudesec`：`claudesec spawn/status/attach`，秘书调它而不是拼 tmux 命令
- 跨项目：一个秘书管多个 repo
- 历史：按天归档 reports

---

## 8. 实现（已完成）

秘书命令（秘书经 `--allowedTools "Bash(claudesec *)"` 免确认调用）：

```
claudesec                       切到/附着 tmux session "claudesec"（不存在则创建，秘书 resume 上次会话）
claudesec --new                 秘书开新会话（session 已存在则原地重启秘书窗口）
claudesec -d                    只在后台建好 session，不切换
claudesec spawn NAME [--dir D] [--model M] [--discuss] [--agent cursor] [--for WORKER] <<< 任务原文
                                                不带 --dir：~/.claudesec/work/NAME（没有就建），运维、通用的活用
claudesec delegate ME --name N [--dir D] [--ack 理由] <<< 任务书   worker 把小活交给 Cursor worker
claudesec wait N                 委托人等 N 的最终汇报（后台 Bash）
claudesec report NAME <<< 汇报       Cursor worker 给秘书的汇报
claudesec remind WHEN TARGET 内容 [--every 1d]   定时任务，到点叫醒秘书；--list [--all]、--cancel ID
claudesec review [--print]       开当天的复盘 worker（整理秘书记忆、提提示词 diff）
claudesec send NAME <<< 原话         秘书给 Cursor worker 说话（粘贴进它的窗口）
claudesec status | log NAME [-n N] | interrupt NAME | close NAME
claudesec peek NAME [-n N]      窗口最后几行 + 它的 claude 忙不忙（收之前看一眼）；输入框 ❯ 后的灰色（SGR 2）文字是
                                Claude Code 的建议或占位，标成「【CC 建议，不是用户输入】」，和用户没发出的草稿区分开
claudesec archive NAME --summary S --tags a,b [--files x,y]
claudesec archive SLUG --subagent --task T --summary S [--tags] [--dir D] <<< subagent 返回值
claudesec lesson TYPE 情况 用户原话
claudesec sessions              不归 claudesec 管的 Claude 会话（读 Claude Code 的 sessions/<pid>.json）
claudesec shelve NAME [--note N] [--force]     停进程、关窗口，只留记录（worker 或外部会话）
claudesec wake NAME [--model M] [--dir D] [<<< 新指令]     --resume 拉起，历史保留
                                                目录不在了：/tmp 下的（重启清掉的）重建成空目录；
                                                redir 记过的搬家照着走；都不是就拒绝并提示 redir / --dir。
                                                换了目录时对话跟着挪（Claude 的 transcript 按启动目录存，Cursor 的按目录 md5）
claudesec redir OLD NEW                         目录搬了（仓库改名）：登记在 OLD 及其下的 worker 都改到 NEW，搬家记进注册表
claudesec --restart                             重载秘书的 prompt 和 hook，对话保留，worker 不动
claudesec adopt SESSION [--as NAME] [--wait]   SESSION = 会话名 / 后台 job id / session id 前缀（已退出的只能用 id）
```

什么算「不归 claudesec 管」：**不看会话名**，看身份和位置。session id 是秘书的（secretary.session）或某个 worker 的，
不算；pane 在 claudesec 自己的 tmux session 里，或者窗口是某个 worker 的（正在 spawn、wake 的 worker 在注册表里
已经有窗口、还没有 session id），也不算。名字靠不住：Claude Code 恢复会话时会先给它起个自动名（如
`agentlodge-da`），过一会儿 `/rename` 才改回 worker 名；外人的会话也可能恰好和某个 worker 同名。
回归测试：`python3 -m unittest discover -s tests`。

接管（adopt）：只接管 idle 的会话；`--wait` 等它连续空闲 30 秒。停在提示符、但自己开的后台任务还在跑的会话
（sessions/<pid>.json 照样写 idle）不算空闲，`sessions`/`status` 显示「后台任务×N」，adopt 和 shelve 都拒绝，
`--wait` 等到它们结束：后台 job 读 daemon 的 jobs/<jobId>/state.json 里的 inFlight；其他会话从 transcript 里数
本进程启动后发起的异步 subagent（含被 SendMessage 叫醒、重新跑起来的已完成 subagent）、后台 Bash、Monitor，减去收到终态 task-notification（completed/failed/killed、
Monitor 到期）或被 TaskStop 的，非 persistent 的 Monitor 过了 timeoutMs 也算结束。SESSION 可以是会话名、后台 job id，
或 session id（至少 8 位前缀）。对话可能在三种地方：
- 前台会话：对话就是它自己的 sessionId。
- 后台会话（daemon 的 bg job，sessions/<pid>.json 里 kind=bg）：没有 pane。
- 挂在后台 job 上的前台客户端（带 parkedJobId）：这个客户端自己的 sessionId 是空的，对话在它挂着的那个 job 里。
  `claudesec sessions` 把它和 job 合成一行显示，接管的是 job 的会话。
- 已退出的会话：没有活进程，只能按 session id（或 ≥8 位前缀）接管，要求在 projects/*/ 里唯一匹配。
  有活进程占着它、已经归某个 worker、或 transcript 里最后的 cwd 已经不存在，都拒绝。
  在 claudesec session 新开窗口，在那个 cwd 里 resume；默认 worker 名取目录名。

秘书和 worker 的 claude 都带 `--system-prompt-snapshot off`。Claude Code 默认在会话第一次请求时录下系统提示，之后每次 resume 原样重发，
后来传的 `--append-system-prompt-file` 不生效：接管来的会话看不到 worker 规则，`--restart` 和 wake 也拿不到改过的规则。
旧版 claude 没有这个选项，就不加。

流程：先确认 projects/*/<sid>.jsonl 存在，不存在就什么都不动直接退出 → 后台 job 用 `claude stop <jobId>` 停
（直接 kill 会被 daemon 重启），等宿主进程退出 → 有 pane 的设 remain-on-exit 并 SIGTERM 旧前台进程；
没有 pane 的（后台、已退出）在 claudesec session 新开窗口 → 注册 worker → `respawn-pane` 在原 cwd 运行
`claude --resume <sid>`，带上 claudesec 的 hooks 和 worker 规则。命令末尾没有 shell，claude 一退出 pane 就死
→ 等 worker 的 SessionStart hook 回写的 session id **等于 <sid>** 才报 adopted 并取消 remain-on-exit。
接着改名：peer 按 sessions/<pid>.json 里的 name 寻址，而 resume 时 transcript 里存的名字（后台 job 的自动名）
会盖过 `-n`，所以在会话里打 `/rename <worker 名>`，等那个 name 变成 worker 名才打印 adopted；
改不过来报 adopt INCOMPLETE（worker 已在跑，只是秘书按名字找不到它）。
claude 退出、id 不一致、60 秒没回写都算失败：worker 从 registry 删掉，死 pane 留着显示错误，
并提示对话完好、可以手动 resume。不 grep 屏幕找错误，因为 resume 会把旧消息原样渲染出来。

已验证：
- 离线：Stop hook 打回 / 放行 / 防死循环；NEED_DECISION 进状态表「等你」列；
  用户决策从 log 中提取；归档文件与 INDEX；SessionStart 注入内容。
- 真机：spawn → PROGRESS → DONE 唤醒空闲秘书；worker 内部开 subagent；hook 自动写 registry/log/session id；
  后台 subagent 等待时 Stop hook 放行，唤醒后正常汇报；archive；close。
- 真机：adopt 一个有历史的临时会话 → 旧进程退出 → 同窗口 resume → 首条汇报准确复述接管前的对话。
- 真机：adopt 一个 `claude --bg` 临时会话（按 job id）→ `claude stop` → 新窗口 resume 同一 session id → 历史在、
  SessionStart 回写一致才报 adopted；claude 立即退出时报 FAILED；transcript 不存在（parked 的空 fork）时不动任何进程。
- 真机：adopt 一个自动命名的 `claude --bg` 临时会话 `--as renamed-test` → sessions 里的 name 从自动名变成
  renamed-test（nameSource=user），之后的轮次不会被改回去。
- 真机：adopt 一个已退出的 `claude -p` 临时会话（按 8 位前缀）→ 新窗口 resume 同一 session id → 历史在 → adopted。

- 真机：`claudesec -d` 起独立 session 里的秘书 → 模拟用户输入派单 → 秘书给收据、开 worker（进同一 session）
  → worker DONE 唤醒秘书 → 秘书推送一行并自行归档 → 「进展」一屏回答。

### Cursor 开关（config `cursor`）

有的机器没装 Cursor。`cursor_available()`：config `cursor` 为 false → 关；否则看 PATH 里有没有 `agent`
（`"auto"` 默认，`true` 也一样要求装了）。不可用时：
- `spawn --agent cursor`、Cursor worker 的 `wake` 拒绝；`delegate` 拒绝并告诉委托人自己做或开 subagent。
- 提示词里 Cursor 相关的段落用 `<!-- if cursor --> … [<!-- else --> …] <!-- end cursor -->` 包起来。
  worker 的 prompt.md 在 spawn / wake 时按开关渲染；秘书的在 `claudesec run` 时渲染成 `~/.claudesec/secretary-prompt.md`
  再传给 claude。关的时候秘书只看到一句「不可用，用户要 Cursor 时告诉用户、改派」，worker 看到「小活自己做或开 subagent」。
- `claudesec status` 末尾一行「Cursor worker：可用（路径）/ 已关 / 没有 agent 命令」。

### worker 把小活委托给 Cursor（`claudesec delegate` / `wait`）

用户定了「独立、小任务尽量派 cursor 做」。第一版让 worker 发 TYPE: DELEGATE 给秘书、秘书 spawn、Cursor 的汇报
经秘书转回：一次委托的任务书和结果各过秘书上下文两遍（约 1–2 万 token），每一步还要秘书推理。现在全由脚本做：
- worker 在 Bash 里跑 `claudesec delegate <自己> --name N [--dir D] <<< 任务书`：当场 `spawn --agent cursor --for <自己>`
  （N 已经是它委托的、还在跑的 Cursor worker 就 send 过去，用来回答问题或追加）。spawn 的首条消息写明「worker X 委托」，
  不抢秘书的焦点。
- 越界检查是关键词规则（push / merge / MR、部署 / 发版 / 上线、凭据 / token / secret / .ssh、对外发消息、rm -rf / drop table），
  命中就拒绝，并告诉委托人命中了什么。由委托人判断（用户定的，不经秘书）：真越界就自己做；字面误伤就加 `--ack "理由"` 重跑，
  理由记日志，并叫醒秘书看一眼。规则宁可误伤：误伤的代价是委托人多跑一次。
- 结果：Cursor 的 `claudesec report` 照常写日志和注册表；委托来的不再发告警。委托人在后台 Bash 跑 `claudesec wait N`：
  从委托那一刻的日志位置往后找第一条 DONE / FAILED / BLOCKED / NEED_DECISION，打印原文后退出。Cursor 的 stop hook
  在出错时写 `ended-error`、没汇报就停下（催够次数、被打断、中止）时写 `ended-unreported`，比委托时间新的就让 wait
  带说明退出；窗口没了、worker 被 close 也退出。按日志位置而不是时间戳判断，是因为追加消息时上一轮可能还没结束，
  它的 stop 晚于委托时间（实测踩过）。
- 不往委托人窗口里粘：Claude Code worker 的窗口可能正开着确认框，也可能用户正在里面打字。后台 Bash 结束会把 worker 叫醒，
  Claude Code 自带的机制，可靠。
- 秘书：只看 `claudesec status`（「[为 X]」）。只为可能要它或用户处理的事叫醒它，即「[委托回执]」一行：FAILED、BLOCKED、
  Cursor 卡在对话框上、`--ack` 放行。用户的取舍：「你只需要知道状态」；也不让委托人另外汇报一句，那要多花一轮，还得靠 LLM 记得发。
- 收尾：Cursor 汇报 DONE 时写 `finish` 标记，这一轮结束（stop hook）后由一个脱离的进程归档（tag delegated）并 close。
  在 stop hook 里直接 close 会杀掉自己所在的窗口。FAILED / BLOCKED / NEED_DECISION 不关，留给委托人追加或放弃。

已验证（临时 CLAUDESEC_HOME，真 Cursor worker）：关键词拒绝；--ack 放行并叫醒秘书；DONE → wait 返回原文 → 自动归档、关窗口、
秘书零告警；NEED_DECISION → wait 返回 → 同名追加答复 → wait 等到第二轮的 DONE（修正前这里会被上一轮的 stop 误判）；
不汇报的一轮被 stop hook 催出 DONE；FAILED → wait 返回、秘书收到一行回执；interrupt 打断 → wait 报「停下没汇报」。
16 个样例句子的关键词命中符合预期（「token 数」「release notes」「merge sort」会误伤，靠 --ack）。
未验证：Claude Code worker 在真实任务里会不会用、后台 wait 结束后能不能顺利接着干（要真用时观察）。

### 主窗口面板（`claudesec dashboard`）

秘书窗口左边是秘书，右边一列四块面板（窗口宽的 36%，上下四等分；顺序是 `DASH_PANES`，`dashboard_side: "left"` 把这一列放到左边）。
从上到下（用户 2026-10-27 定的）：
1. 待办事项，`dashboard show notices`：notices.md 的「待办」，要用户去做、但不用拍板的事（去某窗口说 push、在 VNC 里装扩展）。
   待办和决策队列共用编号序列（queue.last），用户说「46」不会有歧义。
2. 决策队列，`dashboard show queue`：queue.md 的「待问 / 待答」（空的那块不显示；「搁置」折成最后一行；不显示「已处理」）。
3. 任务进展，`dashboard show status`：worker、搁置的名字。最后一行钉着今天的花费（秘书和全部），内容滚动时也不动；
   面板矮于 5 行时不显示这一行。
4. 定时任务，`dashboard show reminders`：未到点的 `claudesec remind`，最早的在上，周期的标「每 1d」。
命令名还是 `notice` / `remind`，只有显示的叫法改了。面板组成变了的版本，旧面板换代（代码变了自动重载）后发现自己的
`@claudesec-layout` 跟新版不同，就在后台跑一次 `dashboard layout` 重排，不用等终端变尺寸或 --restart。
- 默认每条折叠成一行（放不下的截成「…」）；点一条展开看全文、再点收起，点标题栏（或 Tab）全部展开 / 收起。
  点完焦点自动还给秘书 pane（tmux 的点击会先把焦点切到面板，不还的话用户接着打字会打进面板里）。
  组与组之间空一行。展开状态在面板进程里，按条目内容记，刷新不丢。
- 队列：每条打头是它的编号（右对齐），待问的编号颜色按条目末尾的优先级（高红、中黄、低灰），待答的是灰的
  （附「N 天前问的」）；`[tag]` 拆出来作 worker 名高亮。换行不拆英文词和路径，逗号句号不落到行首。
- 四块，像 backlog → todo → doing → done：「搁置」用户说以后再说的（秘书不主动问）、「待问」、「待答」（问过了等回答）、
  「已处理」（新的在上）。旧文件的「已问未答」读作待答；任何 `claudesec queue` 命令先把文件补齐成四块（改名、补标题、
  换掉讲旧分区的说明、并掉成串的空行），条目和编号不动。会话开始注入给秘书的「还有 N 件没定」只算待问 + 待答，搁置另起一行。
- `claudesec queue move N backlog|todo|doing|done [--note 文本]`：挪条目。进待答写上当天「已问」日期，回到待问 / 搁置去掉它；
  进已处理放在最上面，写上当天日期，`--note`（如「用户答：A」）替换问题。省得秘书手写脚本改文件。
- 第五块「记录」（Ideas）：用户只让记下的想法，不是问题。`queue add --idea` / `queue move N ideas`；面板折成最后一行，
  会话开始注入里单列一行，不算「没定」的。
- 容错（2026-10-25 真出过）：秘书把 Read 带行号的输出写回了 queue.md，每行前面多了 1–3 个「数字+Tab」，分区标题认不出来，
  下一条 `queue` 命令以为缺分区，又在末尾补了一整套，之后 move 找不到编号或挪进了后补的那套。现在读的地方（面板、注入、show、
  发号）都先 `repair_queue`：去掉行号前缀、重复的分区合成一个（已处理里后补的那套在前）；任何 `queue` 命令发现文件坏了先原样
  备份到 `~/.claudesec/backups/`、在 stderr 说一声再写回修好的。`queue doctor [--fix]` 另查编号重复、没编号、在分区外的条目，
  有问题时退出码 1。move 找不到编号时退出码 1，说是不是通知、提示 `queue list` / `queue doctor`。
- 年龄：`queue-meta.json` 记每条加入的日期（放在 queue.md 旁边，秘书手改文件不会丢）；待答按「已问」日期算。面板折叠行右边
  显示等了几天（当天不显示），`queue list [--older 天数]` 列出来，之前加的条目不知道日期显示 `?`。
- 编号是给用户回答用的（「12 A」），所以要稳：条目在块之间挪、别的条目删掉，都不变；号只增不复用
  （`queue.last` 记着发到了几）。`claudesec queue add [--asked|--later] 文本` 加到「待问」（或「待答」「搁置」）末尾、打印 `#N`，
  `--asked` 放在文本前后都行，并自动写上当天的「YYYY-MM-DD 已问：」；待答的条目没有日期或日期不对时，面板只是不显示
  「N 天前问的」（有优先级就显示优先级），不会整块报错；
  `claudesec queue number` 给没编号的未定条目补号（老格式 `1.`、`0d.` 也认，补完改成 `- #N`）。没编号的条目面板上显示 `·`。
  不做成面板自己补号：面板是只读的，跟秘书的 Edit 同时写 queue.md 会互相覆盖。
- 进展：按「等你（要你拍板 / 卡住了 / 等你批权限）→ 出错 → 在做（启动中 / 进行中 / 讨论中）→ 做完」排，同组新的在前；
  每个 worker 折叠时一行：标记、名字、状态、距今、token；展开加上最后一次汇报（等你的显示它问的问题）和 token 明细。标题栏是各组计数。
- token（面板和 `claudesec status`，含搁置表）：worker 会话 transcript 加它的 subagents 里每次回复的 usage 之和，
  输入 + 缓存写 + 缓存读 + 输出合成一个数（1.2K / 3.4M）。合成一个数是因为表里放不下四列；大头是缓存读（这台机器上
  通常占九成以上，单价是输入的十分之一），所以展开那行给四项明细。一次回复分几块流式写成几行、usage 相同，按 message id 只算一次。
  Cursor worker 没有能读的 transcript，显示「-」；还没拿到 session id 的也是「-」。transcript 动辄上百 MB，
  `usage.json` 按文件记着读到的位置和累计值，面板每 3 秒只读新追加的部分；没写完的半行留到下次。文件变短（被重写）就从头读。
  history 归档不带 token：归档时会话可能还在继续，数会过时。
- 语言：面板、`claudesec status`、`claudesec queue` 的文字按 `lang()` 走：config `language`（zh / en），不设时看 Claude Code
  settings.json 的 `language` 是否英语，否则中文。不看 LANG：中文用户跑 en_US.UTF-8 的很多（这台机器就是）。字符串就地写
  `tr("中文", "English")`，不另建翻译表。queue.md 读的时候中英文标题都认（`## To ask`、`## Waiting`、`## Backlog`、`## Done`，
  日期标记「已问：」/「asked:」，优先级（高 / high）），补标题按文件已有标题的语言，新建文件按 `lang()`。
  给秘书的告警和 hook 注入也跟着走（见下）；提示词不跟。
- 提示词只有中文一份（`prompts/*.md`），设成 en 也读它：模型不挑语言（用户 2026-10-27 定；曾经的 `prompts/en/`
  英文版和中英对照测试一并删了）。
  代码会产生或识别的短语（`User decision:`、`[claudesec alert]`、`[delegation receipt]`、`[reminder rN]`、`[for X]` 等）
  两边固定对应；识别的地方中英都认（`is_user_decision`、lessons / queue 的 `## Done`、归档索引的 `for X: `）。
  代码里给模型看的字（首条消息、Stop hook 的理由、会话开始注入、告警、回执、归档正文）写成 `tr("中文", "English")`，
  中文分支原样不动；`tests/test_english.py` 查英文下这些都没有中文，中文下仍是原来的字。
- 两块各自能上下滚：鼠标滚轮（tmux `mouse on` 时；面板开了鼠标上报，tmux 把滚轮转给它，不进 copy mode）、
  焦点在面板上时（用 tmux 的切 pane 键切过去；点击会把焦点还给秘书）↑↓ / j k、PgUp PgDn / b 空格、Home End / g G。标题栏不动，上下有「还有 N 行」。
  内容刷新不重置滚动位置。面板用自己的屏幕（alternate screen），重画不会堆进 tmux 的历史。
- 每块面板是个循环：每 3 秒重读文件，内容、尺寸或滚动位置变了才重画；尺寸变了（SIGWINCH）立刻重画，
  刚分出来的面板第一帧就是对的。不抢焦点。秘书的 pane 没了就自己退出，窗口随之关闭（不留下只有面板的窗口）。
- pane 靠 pane 选项 `@claudesec-role`（secretary / queue / status）区分。秘书 pane 的定位、`--restart` 的
  respawn 都改成按这个找，不再按窗口取当前 pane（用户把焦点放在面板上时会认错）。
- 认面板：pane 选项 `@claudesec-role` 之外，起动命令是 `claudesec dashboard show X` 的 pane 也算面板（role X），
  所以没打标记的旧面板 `dashboard off` 也删得掉，也不会被当成秘书 pane。每块面板还带 `@claudesec-layout`（这一版的面板组成和
  左右边）；不带或对不上的（旧版本建的）重排时整列重建。
- `--restart` 在 respawn 秘书之前先排面板：秘书自己在 Bash 里跑 `claudesec --restart -d` 时，这个进程会跟着秘书一起被杀，
  respawn 之后的步骤不会执行（2026-10-23 就是这样：重启了，面板还是旧的）。`claudesec run` 起来时也排一次，不管秘书是怎么起的。
- 布局由 `arrange_dashboard` 定，幂等：窗口 ≥110 列就是三块，更窄不显示（秘书独占）；早先版本的两块，或 110–149 列合成的
  一块（role `both`），会被换成三块。配置 `dashboard: false` 时不显示。每次 `claudesec` / `--restart` / `--new` 都跑一次；session 上挂了 client-resized、
  client-attached 两个 hook 跑 `claudesec dashboard layout`，终端尺寸变了自动重排。整机重启后用户敲 `claudesec`
  重建 session，布局随之恢复（这个 session 本来就不交给 tmux-resurrect）。
- 回归测试 `tests/test_dashboard.py`：布局、换行、队列解析、进展排序、滚动和按键。端到端（隔离的 tmux server，
  pty 里挂一个真 client，往 client 写 SGR 鼠标序列）：130 / 120 列两块、100 列没有；滚轮下 3 次滚 9 行、上 1 次回 3 行；
  点第 3 行展开那一条、焦点回到秘书；点标题全部展开、再点全部收起；G / g 到底 / 回顶；杀掉秘书 pane 面板退出。
- 更早的验证（临时 tmux session、假的秘书进程）：191 列两块、100 列没有、再宽回去恢复；on / off；
  重复 layout 不动已有 pane；新开窗口和 --restart 后面板都在、--restart 只换秘书 pane；杀掉秘书 pane 后面板退出、窗口关闭；
  resize hook 的命令能正确重排。没验证：真用户终端里拖动窗口大小时 client-resized 的触发（tmux 文档行为）。

### 定时任务（`claudesec remind`）

秘书原先拿后台 Bash 的 sleep 当定时器，秘书重启、compact 就丢，status 里也看不到。现在：
- 存储：`~/.claudesec/reminders.json`（加锁读写，同 registry）。每条有 id（r1、r2…）、到点时间（带时区的 ISO）、
  对象（worker 名或 secretary）、原文、状态（pending / fired / cancelled）、投递结果。
  已发和已取消的只留最近 30 条。
- 周期：`--every 30m|12h|1d|7d`。到点后 due 往后推到下一个未来的时间点，错过的几次只补发一次，
  「迟到多久」按最近错过的那次算。
- 检查：`claudesec run` 起 `lifecycle reminders`，每 15 秒一次，只要它那个 run 还是 run.pid 里的那个就一直跑；
  秘书重启后新的检查进程等旧的交出锁再接手。到点的先标 fired 再投递，所以最多投一次。
  秘书没在跑（包括整机重启）时不检查：提醒还在文件里，下次启动时补发，并注明迟到多久。没有用 launchd：
  投递的去处是秘书或 worker，它们都要 claudesec 在跑才有意义。
- 投递：一律写告警叫醒秘书（「[定时任务 rN]」开头），由秘书路由，不直接找 worker。用户定的：「肯定是先找你呀，
  你维护的脚本，你是我的秘书」。也因为秘书能 SendMessage 给在跑的 Claude Code worker，claudesec 自己不能；
  转不转、告不告诉用户（「别打扰我」）也是秘书的判断。曾做过直投 worker 的 --direct（搁置的 wake、Cursor 用 send），
  按用户这句去掉了。
- 跟决策队列不打通：queue.md 是秘书手写、按重要程度排的 markdown，提醒要的是机器能读的时间；
  合在一起，秘书得手改结构化数据、检查进程得解析 markdown。用户也认为决策队列用 md 就够。
  没有时间点的待办照旧进 queue.md 或直接派出去。

已验证（临时 CLAUDESEC_HOME）：13 种时间写法的换算、3 种坏输入报错；登记、过去时间被拒；--list / --cancel；
到点出告警；run 换代后旧检查进程退出、新的接手；改成 22 分钟前到点的提醒补发时带「迟到 22 分钟」；
status 列出未到点的提醒。

### Cursor worker（`spawn --agent cursor`）

跑的是 Cursor 的 `agent` CLI。Claude Code 那一套它都没有：没有按会话装的 hook（`--settings`）、没有
`--append-system-prompt`、没有 SendMessage、没有 sessions/<pid>.json。对应的做法：
- hook 只能装在全局 `~/.cursor/hooks.json`。spawn / wake 时 claudesec 把自己的三条（sessionStart、
  beforeSubmitPrompt、stop）写进去，其他条目不动，文件有变化就先备份成 `hooks.json.bak-claudesec-<时间>`。
  每条都是 `[ -z "$CLAUDESEC_WORKER" ] || claudesec hook cursor-…`：只有 worker 命令设了这个变量
  （hook 进程会继承 agent 的环境，已验证），用户别的 Cursor 会话里什么都不做。
- 规则：sessionStart 返回 `additional_context`（prompts/worker-cursor.md）。只在新会话触发；`--resume` 不触发，
  但第一次注入的内容留在会话里。会话 id 也由 sessionStart 回写。
- 汇报：worker 用 shell 跑 `claudesec report NAME`，记日志、注册表后经告警通道（alerts/ + 秘书的 watcher）叫醒秘书，
  正文以「（不是告警）Cursor worker 的汇报」开头。
- subagent：Cursor 的 subagent（Task 工具）继承 worker 的环境变量和规则，会自己跑 `claudesec report`（实际发生过：
  四个并行 subagent 各报一条 DONE，委托人的 wait 拿到的是 subagent 的 DONE，`finish` 标记还会让 worker 没写完就被
  归档关掉）。Cursor 给每个 shell 设 `CURSOR_CONVERSATION_ID`：worker 本人的等于 sessionStart 记下的会话 id，
  subagent 的是它自己的会话 id（已验证）。`report` 见到不一致就拒收，原文记成 `subagent-report-refused`，
  不进注册表、不叫醒谁。Cursor 2026.10 的 CLI 不触发 subagentStart / subagentStop，所以不靠它们。
- 秘书 → worker：`claudesec send`，tmux 粘贴（bracketed paste，多行算一条）+ 回车。它在忙时消息进 Cursor 的队列，
  这一轮结束后执行。
- 忙/闲：beforeSubmitPrompt 写 busy 标记，stop 清掉。没汇报就停下：stop 返回 `followup_message` 让它补发，
  最多 3 次，再不发就告警。stop 的 status 为 error 时记「API 出错」并告警；但 `claudesec interrupt`（Ctrl+C）
  打断的那一轮也报 error，所以 interrupt 先写标记，30 秒内的非 completed 结束都当作打断。
  已经发过最终汇报（DONE / FAILED / BLOCKED / NEED_DECISION）之后这一轮才以 error 结束的，算正常结束：不改状态、不告警，
  委托来的 DONE 照常归档关窗（实际发生过：开完 MR、报完 DONE，收尾那几句话撞上「Server error」，worker 被标成 API 出错留着没关）。
- shelve / wake：会话在 `~/.cursor/chats/*/<chatId>` 就算还在；wake 用 `agent --resume <chatId>`，
  因为不触发 sessionStart，确认方式是 agent 画出界面并且活过 5 秒。没有 adopt。
- 传输：agent 默认用 HTTP/2 连 agentn.us.api5.cursor.sh，本机（疑似 CorpLink 网关）会卡在「Connection stalled」，
  所以 `~/.cursor/cli-config.json` 要开 `network.useHttp1ForAgent`。不要改用 CURSOR_CONFIG_DIR：那样读不到
  `~/.cursor/hooks.json`，hook 全部失效。spawn / wake Cursor worker 时会检查这一项，没开就打 WARNING，
  说明后果和改法，但不替用户改文件，worker 照样开。只在 macOS 上检查：devbox（Linux）走 HTTP/2 正常。
- Linux（devbox，10-22 试跑）：要 tmux ≥ 3.1（`set-option -p`、`split-window -l N%`；Debian 10 的 apt 只有 2.8，
  devbox 上自编 3.5a 装在 `~/.local`）、Python ≥ 3.8（系统 python3 是 3.7，claudesec 开头就拒绝并说明）。
  git 2.30 没有 `rev-parse --path-format`，所以 `--git-common-dir` 自己按 cwd 解析。Cursor 的
  `approvalMode` 是用户的设置：不是 unrestricted 时，worker 运行 `claudesec report` 会停在「Not in allowlist」上。
- 模型：HTTP/1 下 Grok 4.7 仍每轮以「Server error」结束（交互、-p 都复现），stop 拿到 status=error；Auto 落到 Grok 4.7
  时也一样。默认 `cursor_worker_model: "gpt-5.6-sol[context=1m,reasoning=high,fast=false]"`（GPT-5.6 Sol 1M High，用户 10-22 指定；HTTP/1 下交互 worker 和 -p 都跑通过一轮）。
  不能写 `gpt-5.6-sol-high`：`--list-models` 把它标成「1M High」，实际跑的是 272K（窗口底栏「GPT-5.6 Sol 272K High」）。
  完整的 variant 串才是 1M，参数顺序也得照这个写，换顺序或多加参数会报「Cannot use this model」。agent 每次启动都会把选中的
  模型和参数写回 `~/.cursor/cli-config.json` 的 `model` / `modelParameters`，这是 Cursor 自己的行为，不是 claudesec 改的。
  之前默认 gemini-4-argon-high（本账号可用模型里 CursorBench 4.0 分最高），也能用。

已验证（真机，Cursor agent 2026.10.19-6bbf2c4，临时 CLAUDESEC_HOME）：spawn → PROGRESS、DONE 经告警送达、注册表更新；
send 后它不汇报 → stop 的 followup 催出 DONE；忙时 shelve 被拒，闲时 shelve → wake 带指令 → 同一个会话 id
接着回复并汇报；窗口被杀后 `lifecycle wake-all` 静默恢复；interrupt 打断 shell 命令后状态不变、不告警；
没有 CLAUDESEC_WORKER 的普通 Cursor 会话照常跑完，hook 无输出、注册表不变。

未验证（需要用户真用时观察）：
- 秘书 `/compact` 后靠注入的状态接上在跑的 worker
- 秘书的判断质量：分类、路由、升级门槛、推送克制——这部分只能靠 3.6 的进化机制迭代

## 额度（budget，2026-10-26）

方案见 history/2026-10/2026-10-26-budget.md。记账：usage.json 按「本地小时|模型|速度」分桶，记输入、缓存写 5m、缓存写 1h、
缓存读、输出；`claudesec cost` 按单价表（PRICES，官方定价页核对，config.json 的 prices 可覆盖）折算，未计价的模型不算 0。
预算窗口 budget.json：开始、截止、可选金额。Claude Code worker 每次工具调用前过 PreToolUse 检查（没设预算时只是一次文件测试）：
剩 15%（时间或金额，取先到的）时用 additionalContext 提示收尾，每个窗口一次；到点冻结，拒绝除 SendMessage / ToolSearch 外的所有
工具调用，要求发 BLOCKED（ToolSearch 放行是因为 SendMessage 可能要先加载，实测过）。冻结期间 spawn、wake（quiet 的除外）、delegate
拒绝。`--add` / 新的 `--until` / `--off` 解冻；`--from` 预约的窗口由提醒检查器到点打开。解冻后 claudesec 把「预算已恢复，继续」
粘贴进每个被冻结的 worker 窗口，并告诉秘书。已经在跑的 worker 要等下次 spawn / wake 才有这个 hook。Cursor 不管。

失控告警：提醒检查器（跟着秘书跑）每 5 分钟看一次没有自己额度的 Claude Code worker：预算窗口有金额上限时，这个窗口里花了超过上限的
`runaway_pool_share`（默认 30%）；或者上次汇报已经超过 `runaway_quiet_minutes`（默认 60）分钟、之后花了超过 `runaway_usd`（默认 $20）。
各告警一次（runaway.json 记着），告诉秘书去 peek，要不要收尾或设额度问用户。阈值都在 config.json 里可改。
