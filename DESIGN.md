# cmux 设计文档 —— tmux 里的 Claude 秘书（最小版）

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
3. 会话的自动名每次重启都变（`cmux-48` → `cmux-c4` → `cmux-5e`）→ 秘书必须用固定 `-n` 启动。
4. 新会话可能弹「模型额度」对话框卡住无人看的窗口，且在里面选「切换」会改全局默认模型
   → spawn 一律显式 `--model`，并在 spawn 后 8 秒内抓 pane 检测对话框。
5. `--agent` 的正文会**替换**默认系统提示 → 不用 agent 文件，改用 `--append-system-prompt-file`。
6. worker 等后台 subagent 时会结束本轮；Stop hook 若照常逼汇报，worker 只能发假 BLOCKED
   → Stop hook 输入里 `background_tasks` 非空时放行。

### 3.2 组件（已实现）

```
<repo>/bin/cmux            python3 单文件：秘书用的命令 + 全部 hook 逻辑
<repo>/prompts/secretary.md  秘书宪法（--append-system-prompt-file 追加到默认系统提示）
<repo>/prompts/worker.md     worker 规则，{{NAME}} {{SECRETARY}} 在 spawn 时替换
<repo>/install.sh            只做一件事：软链 bin/cmux 到 ~/.local/bin
~/.cmux/                     状态目录（CMUX_HOME 可改）
  config.json                秘书名、秘书模型、worker 默认模型、worker 权限模式
  registry.json              注册表（3.3），hook 写，加文件锁
  reports/<worker>.log       双向全部消息：task / worker→secretary / secretary→worker
  workers/<name>/            每个 worker 的 prompt.md、task.md、settings.json（hooks）
  history/                   3.5
  lessons.md                 3.6
  secretary.session          秘书 session id，`cmux` 据此 --resume
  secretary-settings.json    秘书的 hooks
```

hook 全部通过 `--settings` 挂在单个会话上，**不碰用户的全局 settings.json**：

| 会话 | 事件 | 做什么 |
|---|---|---|
| worker | SessionStart | 把 session id 写进 registry（溯源第三层） |
| worker | PostToolUse(SendMessage) | 发给秘书的消息 → 追加 log、解析 TYPE、更新 status/summary/pending_ask/focus；关键类型写「已汇报」标记 |
| worker | Stop | 有后台任务 → 放行；有标记 → 清标记放行；`stop_hook_active` → 放行；否则 block 要求汇报 |
| 秘书 | SessionStart（含 compact/resume） | 记 session id；注入状态表 + INDEX 最近 20 行 + 待复盘教训数 |
| 秘书 | PostToolUse(SendMessage) | 发给 worker 的消息 → 追加到该 worker 的 log、更新 focus |

结果：秘书**不记账**。日志、注册表、焦点都由 hook 确定性维护，秘书只负责判断和措辞。

### 3.3 注册表（秘书的外脑）

```json
{
  "focus": "perf",
  "workers": {
    "auth": {
      "session": "cmux-auth-12",
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
| 天～月级 | 做过什么、结果、改动文件、**决定及理由** | `~/.cmux/history/`（下） | 秘书，任务收尾时 | 只有 `INDEX.md` 常驻；正文按需检索 |
| 永久 | 用户偏好、常设指令 | Claude Code 自带 memory + `settings.json` | 秘书，用户给元指令时 | 自带机制负责 |

**历史库**，形状跟 Claude Code 自己的 memory 一样：一事一文件 + 一个索引。

```
~/.cmux/history/
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
- **检索走 subagent**：用户问旧事 → 秘书派 Explore 在 `~/.cmux/history/` 按关键词/日期/
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
| **宪法** | `secretary.md` 的规则 | 秘书在 `~/.cmux/lessons.md` 攒教训；**同类错误 ≥3 次**才提一个 diff 给用户；点头后秘书自己改文件，下次启动生效 | 用户看一眼 |
| **铁律** | 2.5 节四条 | 永不自改 | — |

为什么宪法层要门槛和审批：
- 漂移：规则只加不减会撑爆秘书自己的上下文——正是最防的事。所以提 diff 时必须同时提议删掉什么
- 过拟合：纠正一次就写「从此所有 X 都给 B」
- 没有回归测试：改坏了自己没人发现

`lessons.md` 格式：日期 · 信号类型 · 当时情况一句话 · 用户纠正原话。
秘书每周（或用户说「复盘」时）读一遍，归类计数，达到门槛的提 diff。

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
- Go 二进制 `cmux`：`cmux spawn/status/attach`，秘书调它而不是拼 tmux 命令
- 跨项目：一个秘书管多个 repo
- 历史：按天归档 reports

---

## 8. 实现（已完成）

秘书命令（秘书经 `--allowedTools "Bash(cmux *)"` 免确认调用）：

```
cmux                       切到/附着 tmux session "cmux"（不存在则创建，秘书 resume 上次会话）
cmux --new                 秘书开新会话（session 已存在则原地重启秘书窗口）
cmux -d                    只在后台建好 session，不切换
cmux spawn NAME --dir D [--model M] <<< 任务原文
cmux status | log NAME [-n N] | interrupt NAME | close NAME
cmux archive NAME --summary S --tags a,b [--files x,y]
cmux archive SLUG --subagent --task T --summary S [--tags] [--dir D] <<< subagent 返回值
cmux lesson TYPE 情况 用户原话
cmux sessions              不归 cmux 管的 Claude 会话（读 Claude Code 的 sessions/<pid>.json）
cmux adopt SESSION [--as NAME] [--wait]
```

接管（adopt）：只接管 idle 的会话；`--wait` 等它连续空闲 30 秒。在原 pane 设 remain-on-exit →
SIGTERM 旧进程并等它退出 → `respawn-pane` 运行 `claude --resume <原 session id>` 加 cmux 的 hooks 与
worker 规则 → 取消 remain-on-exit、窗口改名。拉起后的首条消息让它汇报之前在做什么。
claude 退出后 pane 回到 shell。

已验证：
- 离线：Stop hook 打回 / 放行 / 防死循环；NEED_DECISION 进状态表「等你」列；
  用户决策从 log 中提取；归档文件与 INDEX；SessionStart 注入内容。
- 真机：spawn → PROGRESS → DONE 唤醒空闲秘书；worker 内部开 subagent；hook 自动写 registry/log/session id；
  后台 subagent 等待时 Stop hook 放行，唤醒后正常汇报；archive；close。
- 真机：adopt 一个有历史的临时会话 → 旧进程退出 → 同窗口 resume → 首条汇报准确复述接管前的对话。

- 真机：`cmux -d` 起独立 session 里的秘书 → 模拟用户输入派单 → 秘书给收据、开 worker（进同一 session）
  → worker DONE 唤醒秘书 → 秘书推送一行并自行归档 → 「进展」一屏回答。

未验证（需要用户真用时观察）：
- 秘书 `/compact` 后靠注入的状态接上在跑的 worker
- 秘书的判断质量：分类、路由、升级门槛、推送克制——这部分只能靠 3.6 的进化机制迭代
