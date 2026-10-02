# pi-kitmux

Pi Coding Agent 的终端多路复用工具集：用 `fzf` 选择并跳转到运行中的 Pi Agent 所在的 Kitty Tab / Byobu(tmux) Pane，并配套 Pi 扩展实现 Tab 标题的运行状态同步。

## 组成

| 文件 | 说明 |
|---|---|
| `tmux.conf` | tmux / Byobu 配置的唯一事实源。`scripts/deploy.sh` 把它同时软链到 `~/.tmux.conf`（原生 tmux 读取）与 `~/.byobu/keybindings.tmux`（Byobu 的 `profiles/tmuxrc` 第 35 行 source），保证两边配置一致。窗口栏显示「名称@位置」（如 `pi@main` / `pi@wt:5`），一眼区分主仓与各 worktree |
| `switch-pi-agent.py` | 主脚本。扫描进程表找到所有运行中的 Pi Agent，定位其所在的 Kitty Tab / Byobu 窗格与工作目录，用 `fzf` 交互选择后跳转（按 Kitty Tab 分组展示；子行的 tmux 状态栏信息与底部 tab 栏逐格对齐） |
| `kitty-tab-sync.ts` | Pi 扩展。综合 `agent_start` / `agent_settled`、阻塞式 UI prompt 与 watchdog 生命周期，用 pane 级 `@pi_running` / `@pi_done` 作事实源，写逐窗口 `@pi_win`（tmux 窗口栏 ⏳ 运行中 / ✅ 已完成）与会话级 `@pi_total`（kitty 标题 ⏳ N）；真正跑完时发 bell |
| `tmux-pane-command.py` | tmux 状态栏 helper。当 `pane_current_command` 只能看到沙盒 wrapper `enclave` 时，从前台 leader 的完整 `enclave run ...` 启动命令直接提取真实应用名 |
| `pi-tab-monitor.sh` | 早期轮询方案：后台循环用 `pgrep` 检测 pi 进程并改写终端标题（已被 `kitty-tab-sync.ts` 事件驱动方案取代，保留备用） |
| `scripts/check.sh` | 一键验证：pre-commit 静态检查（ruff / codespell / vulture / mypy / pyright / pylint）+ 单元测试 |
| `scripts/deploy.sh` | 部署：把 `tmux.conf` 软链到 `~/.tmux.conf` 与 `~/.byobu/keybindings.tmux`，把两个 tmux helper 软链到 `~/.local/bin/`，把 Pi 扩展软链到 `~/.pi/agent/extensions/`。首次运行会把已存在的真实文件备份为 `*.bak.<时间戳>` |
| `tests/` | Python helper 的单元测试 |

## 工作原理

`switch-pi-agent.py`（纯标准库，无第三方依赖）：

1. **进程发现**：单次 `ps axo pid,ppid,command` 构建进程表，按命令行特征（`pi` / `pi-coding-agent`）筛选出 Pi Agent 进程。
2. **位置映射**：
   - Kitty：`kitty @ ls` 列出所有 tab 及其 pane 的 pid；tab 标题按 `kitty.conf` 的 `tab_title_template` 动态渲染，与 tab bar 显示一致
   - Byobu/tmux：`tmux list-panes -a` 按 `pane_pid` 索引，结合祖先链把 Agent 映射到 session / window / pane
   - 工作目录：对匹配到的少量 pid 用 `lsof -d cwd` 查询
3. **选择跳转**：`fzf` 列表按 Kitty Tab 分组，组头是「tab 标题 · session」，组内每个 agent 一行；子行的 tmux 部分与状态栏逐格对齐 ——「`窗口号: ⏳/✅ 名称@位置 ◉/●`」，其中 `⏳/✅`、`@位置`、红 `◉`（bell）/青 `●`（activity）都由 tmux 的 `@pi_win` / `@pi_win_fmt` / `window_bell_flag` / `window_activity_flag` 直接取值，enclave 窗口缺失的应用名由 `tmux-pane-command.py` 现补，故不会与 tab 栏漂移；session 已上移到父节点，子行按窗口号排序，顺序也与 tab 栏一致。选中组头只聚焦该 tab，选中子行则同时切到具体 session/window/pane（只动目标 client，不会误动当前 tab 自己的 client）。无 Kitty Tab 的兜底组仍在子行保留 session 名。

安全细节：

- kitty socket 自动发现（`KITTY_LISTEN_ON` > `/tmp/mykitty-*` 最新 > 默认探测），避免连死 socket 挂起
- 所有外部命令带 3 秒超时，绝不阻塞
- 自身进程（`switch-pi-agent`）会从匹配中排除

`tmux-pane-command.py`：

- tmux 原生 `#{pane_current_command}` 只返回前台进程组 leader 的程序名；执行 `enclave run pi` 时因此只能显示 `enclave`
- helper 根据 `#{pane_pid}` 读取 pane 的 TPGID，再读取该前台 leader 的完整命令行，直接从 `enclave run [options] [--] <command>` 提取 `<command>`
- `.tmux.conf` 的窗口状态格式只在 `pane_current_command == enclave` 时通过异步 `#(...)` 调用 helper，普通程序显示实时的 tmux `pane_current_command`（前台命令名）
- 读取失败或进程切换竞态时安全回退为 `enclave`；不会遍历或猜测沙盒中的子进程

窗口栏位置标签（`tmux.conf`）：

- 统一为「名称@位置」，位置由 `@pi_where` 计算：路径含 `/.worktrees/` 显示 `wt:<目录名>`（即分支名），否则显示 `main`
- 因此 enclave 窗口显示 `pi@main` / `pi@wt:5`；非 enclave 窗口显示 `#W@<位置>`（如 `zsh@main`）
- kitty 标题（`set-titles-string`）用 `@pi_repo`：去掉 `/.worktrees/...` 后取仓库根目录名，故同一仓库的所有 worktree 固定显示同一个 basename（如 `pi-kitmux`），不再随 worktree 变化
- tab 正文抽成 `@pi_win_fmt`，`window-status-format` 与 `window-status-current-format` 用 `#{E:@pi_win_fmt}` 共用，避免两行漂移

`kitty-tab-sync.ts`：

- pane 级 `@pi_running` / `@pi_done` 是事实源（pane 销毁自动清除，不残留）：前者表示本 pane 仍在处理，后者表示上一轮已真正跑完且未被新一轮覆盖
- 窗口级 `@pi_win`：tmux/byobu 窗口栏每格显示三态——有 pane 在跑为 `⏳`（优先于 ✅）、全部跑完为 `✅`、否则为空（均不带数字）；`✅` 持续到该 pane 下一轮运行、出现阻塞式 prompt、切换 session（`/new` / `/resume` / `/fork`）或进程退出
- 会话级 `@pi_total`：本 session 运行中的 agent 总数 `⏳ N`，供 kitty tab 标题（`set-titles-string`）；放 session 级，新开的 tmux 窗口也能立即显示
- 与 `pi-extension-watchdog` 通过 `pi.events` 同步生命周期：watchdog 的 `running` 只表示它自己处于监控/armed 状态（空会话自启动时也会为真），因此仅在**本进程已跑过至少一轮**后，才用它作为「单轮 `agent_settled` 后仍会续跑」的抑制项继续保持 ⏳；否则空会话会误亮。用户按 `Esc` 中止一轮时 watchdog 会广播 `interrupted: true`（此时 `running` 仍为真）：表示本次空闲不会再续跑，故立即清掉 ⏳，且不置 ✅、不响铃——这一轮并非「真正跑完」；等用户发下一条真实消息、watchdog 清回 `interrupted: false` 后恢复正常。只有 watchdog 停止/挂起且当前 agent 已结束时才置 ✅ 并响铃
- 阻塞式 UI prompt（如 plan 评审）期间临时视为等待用户，不显示运行中；prompt 结束后按 agent/watchdog 真值恢复
- 跑完时发送 bell（`\a`）：kitty 的 `bell_on_tab` 会给未聚焦窗口的 tab 加铃铛
- 所有对 tmux 的写调用收在 `StatusSink` 后面，便于将来接远端 sink

## 使用

### 依赖

- Python ≥ 3.14（仅标准库）
- [`uv`](https://docs.astral.sh/uv/)（用于 dev 依赖与测试）
- 运行时依赖：`kitty`、`tmux`（Byobu 底层即是 tmux）、`fzf`

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

`fzf` 交互：列表按 Kitty Tab 分组，组头是该 tab 的标题，组内 agent 缩进列出，子行形如
`1: pi-kitmux · win-2`
`└ 73714  2: ⏳ pi@main ◉`（`父节点：项目目录 · session；子节点：窗口号: 标记 名称@位置 灯 [cwd:<目录>]`，
与底部 tab 栏一致；单窗口 session 不显示窗口号）。父节点已经显示项目名时，子行不重复显示 cwd；只有项目子目录、无法取得父节点项目名，或无 Kitty Tab 时才显示 `cwd:<目录>`。worktree 根目录如果 `@wt:<名称>` 已经表达了 cwd 名称，也会省略。`↑/↓` 选择、`Enter` 跳转
（选中组头只聚焦该 tab，选中子行则同时切到具体 session/window/pane）、`Esc` 取消。
输入查询词过滤时，未命中的组头会一并隐藏。未检测到 Agent 时会提示退出。

### Pi 扩展安装

把 `kitty-tab-sync.ts` 放入 pi 扩展目录（如 `~/.pi/agent/extensions/`）即可，pi 会自动加载并在 agent 启动/结束时同步状态。

### 开发

```bash
uv sync --dev          # 安装 dev 依赖
./scripts/check.sh     # 静态检查 + 测试
```
