# pi-kitmux

Pi Coding Agent 的终端多路复用工具集：用 `fzf` 选择并跳转到运行中的 Pi Agent 所在的 Kitty Tab / Byobu(tmux) Pane，并配套 Pi 扩展实现 Tab 标题的运行状态同步。

## 组成

| 文件 | 说明 |
|---|---|
| `switch-pi-agent.py` | 主脚本。扫描进程表找到所有运行中的 Pi Agent，定位其所在的 Kitty Tab / Byobu 窗格与工作目录，用 `fzf` 交互选择后跳转 |
| `kitty-tab-sync.ts` | Pi 扩展。综合 `agent_start` / `agent_settled`、阻塞式 UI prompt 与 watchdog 生命周期，用 pane 级 `@pi_running` 作事实源，写逐窗口 `@pi_win`（tmux 窗口栏 ⏳）与会话级 `@pi_total`（kitty 标题 ⏳ N）；真正跑完时发 bell |
| `pi-tab-monitor.sh` | 早期轮询方案：后台循环用 `pgrep` 检测 pi 进程并改写终端标题（已被 `kitty-tab-sync.ts` 事件驱动方案取代，保留备用） |
| `scripts/check.sh` | 一键验证：pre-commit 静态检查（ruff / codespell / vulture / mypy / pyright / pylint）+ 单元测试 |
| `scripts/deploy.sh` | 部署：把 `switch-pi-agent.py` 和 `kitty-tab-sync.ts` 分别软链到 `~/.local/bin/` 与 `~/.pi/agent/extensions/` |
| `tests/` | `switch-pi-agent.py` 的单元测试 |

## 工作原理

`switch-pi-agent.py`（纯标准库，无第三方依赖）：

1. **进程发现**：单次 `ps axo pid,ppid,command` 构建进程表，按命令行特征（`pi` / `pi-coding-agent`）筛选出 Pi Agent 进程。
2. **位置映射**：
   - Kitty：`kitty @ ls` 列出所有 tab 及其 pane 的 pid；tab 标题按 `kitty.conf` 的 `tab_title_template` 动态渲染，与 tab bar 显示一致
   - Byobu/tmux：`tmux list-panes -a` 按 `pane_pid` 索引，结合祖先链把 Agent 映射到 session / window / pane
   - 工作目录：对匹配到的少量 pid 用 `lsof -d cwd` 查询
3. **选择跳转**：`fzf` 列表展示 `PID │ Kitty Tab │ Byobu Window │ CWD`，选中后聚焦目标 Kitty Tab，并将目标所在 tmux client 精确切到对应 session/window/pane（不会误动当前 tab 自己的 client）。

安全细节：

- kitty socket 自动发现（`KITTY_LISTEN_ON` > `/tmp/mykitty-*` 最新 > 默认探测），避免连死 socket 挂起
- 所有外部命令带 3 秒超时，绝不阻塞
- 自身进程（`switch-pi-agent`）会从匹配中排除

`kitty-tab-sync.ts`：

- pane 级 `@pi_running` 是唯一事实源（pane 销毁自动清除，不残留）
- 窗口级 `@pi_win`：tmux/byobu 窗口栏每格只显示本窗口跑没跑（`⏳`，不带数字）
- 会话级 `@pi_total`：本 session 运行中的 agent 总数 `⏳ N`，供 kitty tab 标题（`set-titles-string`）；放 session 级，新开的 tmux 窗口也能立即显示
- 与 `pi-extension-watchdog` 通过 `pi.events` 同步生命周期：单轮 `agent_settled` 后若 watchdog 仍会催促，继续保持 ⏳；只有 watchdog 停止/挂起且当前 agent 已结束时才清状态并响铃
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
# 软链主脚本与 Pi 扩展到对应目录
./scripts/deploy.sh

# 运行（或在 ~/.tmux.conf 中绑定快捷键弹出执行）
~/.local/bin/switch-pi-agent.py
```

`fzf` 交互：`↑/↓` 选择、`Enter` 确认跳转、`Esc` 取消。未检测到 Agent 时会提示退出。

### Pi 扩展安装

把 `kitty-tab-sync.ts` 放入 pi 扩展目录（如 `~/.pi/agent/extensions/`）即可，pi 会自动加载并在 agent 启动/结束时同步状态。

### 开发

```bash
uv sync --dev          # 安装 dev 依赖
./scripts/check.sh     # 静态检查 + 测试
```
