# pi-kitmux

Pi Coding Agent 的终端多路复用工具集：用 `fzf` 选择并跳转到运行中的 Pi Agent 所在的 Kitty Tab / Byobu(tmux) Pane，并配套 Pi 扩展实现 Tab 标题的运行状态同步。

## 组成

| 文件 | 说明 |
|---|---|
| `tmux.conf` | tmux / Byobu 配置的唯一事实源。`scripts/deploy.sh` 把它同时软链到 `~/.tmux.conf`（原生 tmux 读取）与 `~/.byobu/keybindings.tmux`（Byobu 的 `profiles/tmuxrc` 第 35 行 source），保证两边配置一致。窗口栏显示「名称@位置」（如 `pi@main` / `pi@wt:5`），一眼区分主仓与各 worktree |
| `switch-pi-agent.py` | 主脚本。扫描进程表找到所有运行中的 Pi Agent，定位其所在的 Kitty Tab / Byobu 窗格与工作目录，用 `fzf` 交互选择后跳转（按 Kitty Tab 分组展示；子行的 tmux 状态栏信息与底部 tab 栏逐格对齐；✅ 子行在名称后附完成时刻，头部显示本次弹窗的加载耗时） |
| `kitty-tab-sync.ts` | Pi 扩展。综合 `agent_start` / `agent_settled`、阻塞式 UI prompt 与 watchdog 生命周期，用 pane 级 `@pi_running` / `@pi_done`（并记录完成时刻 `@pi_done_at`）作事实源，写逐窗口 `@pi_win`（tmux 窗口栏 ⏳ 运行中 / ✅ 已完成待关注）与会话级 `@pi_total`（kitty 标题 ⏳N ✅M）；跑完发 BEL 让 kitty 的 Dock 图标跳动 |
| `tmux-pane-command.py` | tmux 状态栏 helper。当 `pane_current_command` 只能看到沙盒 wrapper `enclave` 时，从前台 leader 的完整 `enclave run ...` 启动命令直接提取真实应用名 |
| `tmux-pane-repo.py` | tmux 状态栏 helper。用 git 求出 pane 工作目录的 `<项目名>/<位置>`：主仓为 `main`，linked worktree 为 `wt:<worktree 名>`，非 git 目录输出空串。一次调用同时供 `@pi_repo` 与 `@pi_where`，也是 picker 侧同一事实源（`#()` 在 `list-panes` 里不执行，只能另调） |
| `tmux-pi-ack.py` | tmux 状态栏 helper（`after-select-window` / `after-select-pane` hook，以及 `MouseDown1Status` 绑定调用）。切到或点中某个 window 时把该 window 的 ✅（done-unseen）清成已读，并立即重算 `@pi_win` / `@pi_total`；负责「看一眼即已读」的即时生效 |
| `pi-tab-monitor.sh` | 早期轮询方案：后台循环用 `pgrep` 检测 pi 进程并改写终端标题（已被 `kitty-tab-sync.ts` 事件驱动方案取代，保留备用） |
| `scripts/check.sh` | 一键验证：pre-commit 静态检查（ruff / codespell / vulture / mypy / pyright / pylint）+ 单元测试 |
| `scripts/deploy.sh` | 部署：把 `tmux.conf` 软链到 `~/.tmux.conf` 与 `~/.byobu/keybindings.tmux`，把四个 tmux helper 软链到 `~/.local/bin/`，把 Pi 扩展软链到 `~/.pi/agent/extensions/`。首次运行会把已存在的真实文件备份为 `*.bak.<时间戳>` |
| `tests/` | Python helper 的单元测试 |

## 工作原理

`switch-pi-agent.py`（纯标准库，无第三方依赖）：

1. **进程发现**：单次 `ps axo pid,ppid,command` 构建进程表，按命令行特征（`pi` / `pi-coding-agent`）筛选出 Pi Agent 进程。
2. **位置映射**：
   - Kitty：`kitty @ ls` 列出所有 tab 及其 pane 的 pid；tab 标题按 `kitty.conf` 的 `tab_title_template` 动态渲染，与 tab bar 显示一致
   - Byobu/tmux：`tmux list-panes -a` 按 `pane_pid` 索引，结合祖先链把 Agent 映射到 session / window / pane
   - 工作目录：对匹配到的少量 pid 用 `lsof -d cwd` 查询
3. **选择跳转**：`fzf` 列表按 Kitty Tab 分组，组头是「tab 标题 · session」，组内每个 agent 一行；子行的 tmux 部分与状态栏逐格对齐 ——「`窗口号: ⏳/✅ 名称@位置`」，其中 `⏳/✅`（pane 级 `@pi_running` / `@pi_done`）取自 tmux，✅ 行末尾另附 `(完成时刻)`（pane 级 `@pi_done_at` 的 epoch 秒，同日显示 `HH:MM:SS`、跨日显示 `MM-DD HH:MM`），`@位置` 与项目名由 `tmux-pane-repo.py` 现调（与状态栏同一事实源），enclave 窗口缺失的应用名由 `tmux-pane-command.py` 现补，故不会与 tab 栏漂移；session 已上移到父节点，子行按窗口号排序，顺序也与 tab 栏一致。选中组头只聚焦该 tab，选中子行则同时切到具体 session/window/pane（只动目标 client，不会误动当前 tab 自己的 client）。无 Kitty Tab 的兜底组仍在子行保留 session 名。头部显示全局「⏳N 运行中 · ✅M 待关注」计数，以及本次弹窗的加载耗时（进程启动到 `fzf` 弹出之间的收集耗时）。

安全细节：

- kitty socket 自动发现（`KITTY_LISTEN_ON` > `/tmp/mykitty-*` 最新 > 默认探测），避免连死 socket 挂起
- 所有外部命令带 3 秒超时，绝不阻塞
- 自身进程（`switch-pi-agent`）会从匹配中排除

`tmux-pane-command.py`：

- tmux 原生 `#{pane_current_command}` 只返回内核态程序名：执行 `enclave run pi` 时只能显示 `enclave`；直接跑 `pi`（node shebang 脚本）时只能显示 `node`（`process.title` 改不了内核名，只改 ps 的 argv 区）
- helper 根据 `#{pane_pid}` 读取 pane 的 TPGID，再读取该前台 leader 的完整命令行：leader 是 `enclave run ...` 就提取 `<command>`；否则 leader 的 ps 首个 token 是真实应用名（pi 会把 `process.title` 设为 `pi`，抹掉其余 argv），不是裸解释器（如 `node server.js`，回退保留内核名）才采用
- `.tmux.conf` 的窗口状态格式只在 `pane_current_command` 为 `enclave` 或 `node` 时通过异步 `#(...)` 调用 helper，普通程序显示实时的 tmux `pane_current_command`（前台命令名）
- 读取失败或进程切换竞态时安全回退；不会遍历或猜测沙盒中的子进程

`tmux-pi-ack.py`（ack helper）：

- 语义：**看一眼即已读**。`✅` 表示「跑完但还没看过」；切到该 window（`after-select-window`）、切到该 pane（`after-select-pane`），或点状态栏上该 window 的 tab（`MouseDown1Status` 绑定）即算看过，`✅` 消失
- 做法：hook / 绑定以 `#{window_id}` 调本脚本，脚本清掉该 window 内各 pane 的 `@pi_done` 与配套的 `@pi_done_at`，再立刻重算 `@pi_win` / `@pi_total` 并 `refresh-client -S`
- 为何单窗口也点得动：点当前 window 时窗口没变化，`after-select-window` 与 `session-window-changed` 都不发；`MouseDown1Status` 对「点当前 window」仍会触发，正好补上这条路
- 为何要重算：`@pi_win` / `@pi_total` 是扩展从 pane 级事实源算出的投影；若只清 `@pi_done` 而不重算，状态栏上的 `✅` 会残留到扩展的下一个事件——而空转的 agent 可能永远等不到下一个事件
- helper 未部署时 hook 静默跳过；重算规则与 `kitty-tab-sync.ts` 的 `broadcastStatus()` 保持一致（改一处要同步另一处）

窗口栏位置标签（`tmux.conf` + `tmux-pane-repo.py`）：

- 统一为「名称@位置」，位置与项目名都由 `tmux-pane-repo.py` 用 git 求出：pane 在 linked worktree 内显示 `wt:<worktree 名>`，在主仓内显示 `main`，**不在 git 仓库内则不显示 `@位置`**（只留名称）
- 因此 worktree 显示 `pi@wt:5`；非 enclave 窗口显示 `#W@<位置>`（如 `zsh@main`）；非 git 目录显示 `zsh`
- `git rev-parse --git-dir` 与 `--git-common-dir` 相等即主仓，不等即 linked worktree；项目名取 common-dir 的父目录名，故同一仓库的所有 worktree 同名
- 之所以不用纯格式串：判断「是否在 worktree 的子目录里」需要取回 worktree 根名，而 tmux 的 `s|A|B|` 不支持反向引用；`#{b:pane_current_path}` 只能给 cwd 的 basename（子目录里会得出 `wt:src`）
- helper 输出一行 `<项目名>/<位置>`，`@pi_repo` 取前半、`@pi_where` 把前半换成 `@`。`@pi_loc` 只写一次 `#(...)`：同一格式串里重复出现的同一命令 tmux 只执行一次，不必为两个消费者 fork 两次 git
- kitty 标题（`set-titles-string`）用 `@pi_repo`，即仓库根目录名，故同一仓库的所有 worktree 固定显示同一个 basename（如 `pi-kitmux`），不再随 worktree 变化；非 git 目录没有项目名，回退到 cwd 的 basename（与改动前的行为一致）
- `switch-pi-agent.py` 的 picker 也从中取位置与项目名：`#()` 只在状态栏/标题这类持久格式串里执行，`tmux list-panes` 拿到的是空串（`@pi_win_fmt`、`@pi_repo`、`@pi_where` 都直接或间接来自 `#()`），故 `pane_location()` 直调同一个 helper 复算，规则不重抄
- tab 正文抽成 `@pi_win_fmt`，`window-status-format` 与 `window-status-current-format` 用 `#{E:@pi_win_fmt}` 共用，避免两行漂移
- 窗口栏只保留 Pi 状态标记（`@pi_win` 的 `⏳` / `✅`），**删掉**原生 `window_bell_flag` 的红 `◉` 与 `window_activity_flag` 的青 `●`：`◉` 与 `✅` 语义重复且会被任意程序的 bell 误触发，`●`（非当前窗口有输出）对常驻输出的 agent 几乎常亮、不携带信息。这里删的只是 tmux 里那个红色 `◉` 装饰；扩展照旧发 BEL，用于触发 kitty 的 Dock 跳动
- 「已完成」的清除靠 `after-select-window` / `after-select-pane` hook 与 `MouseDown1Status` 绑定（见 `tmux-pi-ack.py`）；后者覆盖「点当前 window 的 tab」这种不触发前两者的情形，`✅` 因此是「待关注」而非永久粘滞标记

`kitty-tab-sync.ts`：

- pane 级 `@pi_running` / `@pi_done` / `@pi_done_at` 是事实源（pane 销毁自动清除，不残留）：`@pi_running` 表示本 pane 仍在处理；`@pi_done` 表示上一轮已真正跑完且**尚未被看过**；`@pi_done_at` 是置 `@pi_done` 时的 epoch 秒，与 `@pi_done` 同生同灭，供 picker 在 ✅ 后显示完成时刻
- 窗口级 `@pi_win`：tmux/byobu 窗口栏每格显示三态——有 pane 在跑为 `⏳`（优先于 ✅）、有 pane 跑完待关注为 `✅`、否则为空（均不带数字）；`✅` 会在该 pane 下一轮运行、出现阻塞式 prompt、切换 session（`/new` / `/resume` / `/fork`）、进程退出，或**切到 / 点中该 window（ack，见 `tmux-pi-ack.py`）**时清除
- 会话级 `@pi_total`：本 session 计数 `⏳N ✅M`（N=运行中、M=已完成待关注；为 0 的部分省略，全 0 则整段不显示），供 kitty tab 标题（`set-titles-string`）；放 session 级，新开的 tmux 窗口也能立即显示
- 与 `pi-extension-watchdog` 通过 `pi.events` 同步生命周期：watchdog 的 `running` 只表示它自己处于监控/armed 状态（空会话自启动时也会为真），因此仅在**本进程已跑过至少一轮**后，才用它作为「单轮 `agent_settled` 后仍会续跑」的抑制项继续保持 ⏳；否则空会话会误亮。用户按 `Esc` 中止一轮时 watchdog 会广播 `interrupted: true`（此时 `running` 仍为真）：表示本次空闲不会再续跑，故立即清掉 ⏳，且不置 ✅——这一轮并非「真正跑完」；等用户发下一条真实消息、watchdog 清回 `interrupted: false` 后恢复正常。只有 watchdog 停止/挂起且当前 agent 已结束时才置 ✅
- 阻塞式 UI prompt（如 plan 评审）期间临时视为等待用户，不显示运行中；prompt 结束后按 agent/watchdog 真值恢复
- 跑完发 BEL（`\a`）：kitty 的 `window_alert_on_bell`（默认 `yes`）收到 BEL 会让 Dock 图标跳动，这是 kitty 切到后台时唯一看得见的完成提醒（tmux 窗口栏 `✅`、kitty 标题 `✅M` 在后台都看不到）。tmux 默认会把 pane 的 BEL 转发给外层终端，与该 window 是否为当前窗口无关；BEL 只在真正跑完时发（Esc 打断、退出、`session_start` 校正、弹窗暂停都不发）。kitty 的 `🔔` tab 徽标（`bell_on_tab`）走 `tab_title_template` 的 `{bell_symbol}`，是另一回事
- 所有对 tmux 的写调用收在 `StatusSink` 后面，便于将来接远端 sink

## 使用

### 依赖

- Python ≥ 3.14（仅标准库）
- [`uv`](https://docs.astral.sh/uv/)（用于 dev 依赖与测试）
- 运行时依赖：`kitty`、`tmux`（Byobu 底层即是 tmux）、`fzf`、`git` ≥ 2.31（`tmux-pane-repo.py` 用 `rev-parse --path-format=absolute`）

### 日常使用

```bash
# 软链 tmux.conf、主脚本、tmux helper 与 Pi 扩展到对应目录
# （仓库成为 ~/.tmux.conf 与 ~/.byobu/keybindings.tmux 的唯一事实源，改仓库即生效）
./scripts/deploy.sh

# 重新加载 tmux 配置，使 enclave 窗口名显示其真实应用
tmux source-file ~/.tmux.conf

# 运行（或在 ~/.tmux.conf 中绑定快捷键弹出执行）
~/.local/bin/switch-pi-agent.py
```

`fzf` 交互：头部显示全局计数（⏳N 运行中 · ✅M 待关注），列表按 Kitty Tab 分组，组头是该 tab 的标题，组内 agent 缩进列出，子行形如
`1: pi-kitmux · win-2`
`└ 73714  2: ⏳ pi@main`（`父节点：项目目录 · session；子节点：窗口号: 标记 名称@位置 [cwd:<目录>]`，
标记取 pane 级 `@pi_running` / `@pi_done`（⏳/✅），与底部 tab 栏一致；组内按 ✅ 待关注 → ⏳ 运行中 → 空闲排序，
待关注项浮到每组顶部（组间顺序不变，仍镜像 tab 栏）；单窗口 session 不显示窗口号）。父节点已经显示项目名时，子行不重复显示 cwd；只有项目子目录、无法取得父节点项目名，或无 Kitty Tab 时才显示 `cwd:<目录>`。worktree 根目录如果 `@wt:<名称>` 已经表达了 cwd 名称，也会省略。`↑/↓` 选择、`Enter` 跳转
（选中组头只聚焦该 tab，选中子行则同时切到具体 session/window/pane）、`Esc` 取消。
输入查询词过滤时，未命中的组头会一并隐藏。未检测到 Agent 时会提示退出。

### Pi 扩展安装

把 `kitty-tab-sync.ts` 放入 pi 扩展目录（如 `~/.pi/agent/extensions/`）即可，pi 会自动加载并在 agent 启动/结束时同步状态。

### 开发

```bash
uv sync --dev          # 安装 dev 依赖
./scripts/check.sh     # 静态检查 + 测试
```
