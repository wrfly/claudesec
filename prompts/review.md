每日复盘（{{DATE}}）：整理秘书的记忆，把对谁都适用的教训提成提示词 diff。按 worker 规则汇报。

材料：
- 秘书的 memory：`{{MEMORY}}`，`MEMORY.md` 是索引（一行一条），其余每个文件一条记忆。
- 教训：`{{LESSONS}}`。「已处理」一节以上是待复盘的，格式「日期 · 类型 · 情况 · 用户原话」。
- 提示词：claudesec 仓库 `{{REPO}}` 里的 `prompts/secretary.md`、`prompts/worker.md`、`prompts/worker-external.md`。
- 决策队列 `{{QUEUE}}`、历史索引 `{{INDEX}}`：判断哪些记忆已经过时时参考，不要改它们。

## 一、整理记忆（直接落盘，不用问）

- 合并说同一件事的；删掉已经过时的（对照仓库现在的代码和提示词、决策队列「已处理」、历史索引核实，别凭印象删）；
  相关的互相补上 `[[name]]` 链接；`MEMORY.md` 每条记忆一行 `- [标题](文件) — 一句话`，删掉指向不存在文件的行。
- 每条记忆保持一个事实；feedback / project 类要有 **Why:** 和 **How to apply:**。
- 已经写进提示词、而且下面第二步不会删的规则，memory 里可以只留一行指向它，不用重复全文。
- 派你之前 claudesec 已经把整个目录备份到 `{{REVIEWS}}/{{DATE}}-memory-before/`，出了错从那里还原。
- 这个目录就是你这次的 auto memory 目录：用 Read / Write / Edit 改不弹确认框。整条删掉只能 `rm`，会问一次，攒到最后一起删。

## 二、提示词 diff（只提，不落到 main）

1. 读教训和 feedback 类记忆，归类计数。只有「对谁都适用、跟这台机器和具体项目无关」的才进提示词；
   机器/项目专属的、这个用户个人的偏好（比如某类活爱交给谁做、回复用什么语言）留在 memory。纠正一次就写死成「从此所有 X 都…」是过拟合，要么写成判断标准，要么先不提。
2. 每加一条，同时找能删、能合并的：重复的、已经被代码保证的、过时的。提示词只加不减会撑爆秘书的上下文。
3. **「铁律」一节一个字都不许改。**
   `<!-- if X --> … <!-- else --> … <!-- end X -->`（X 为 agents/ 下的 agent 名，如 cursor / codex / ttadk，或 delegate）包着的是只在机器上有那种 agent 时才给出的内容（delegate：有一种能被 `claudesec delegate` 开出来），可以嵌套，改的时候保留标记。
4. 按仓库的 worktree 规则开新 worktree，分支 `review/{{DATE}}`；改完跑通 python3 -m unittest discover -s tests，commit，**不要 push**。
   `git diff origin/main` 存到 `{{REVIEWS}}/{{DATE}}.diff`。
5. 汇报 NEED_DECISION：DID 写记忆改了什么（几条合并、删了哪些）；ASK 列每一处提示词改动，一行一条，
   标「加 / 删 / 改」和依据（哪几条教训或记忆），附 diff 文件路径；选项给「全部同意 / 只要第 N 条 / 都不要」。
6. 秘书转来「用户决策：」后：按用户选的保留，fetch、rebase 到 origin/main，push（用户已同意 claudesec 的改动 push），
   拆 worktree；被采纳的教训在 `{{LESSONS}}` 里移到「已处理」节，后面注明进了哪条规则。然后发 DONE。

没有值得进提示词的教训：跳过第二步，记忆整理完直接发 DONE，RESULT 写「提示词无改动」和原因。
