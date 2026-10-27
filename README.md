# pi-kitmux

Pi Coding Agent 的终端多路复用工具集：用 `fzf` 选择并跳转到运行中的 Pi Agent 所在的 Kitty Tab / Byobu(tmux) Pane，并配套 Pi 扩展实现 Tab 标题的运行状态同步。

## 组成

| 文件 | 说明 |
|---|---|
| `tmux.conf` | tmux / Byobu 配置的唯一事实源，Mac 与 VPS 同一份。`scripts/deploy.sh` 把它同时软链到 `~/.tmux.conf`（原生 tmux 读取）与 `~/.byobu/keybindings.tmux`（Byobu 的 `profiles/tmuxrc` 第 35 行 source），保证两边配置一致。窗口栏显示「名称@位置」（如 `pi@main` / `pi@wt:5`），一眼区分主仓与各 worktree |
| `tmux.darwin.conf` | macOS 专属配置（目前只有 `bind v` 用 `open -a` 打开 VS Code）。由 `tmux.conf` 末尾的 `if-shell` 在 Darwin 上 source，Linux 上不加载；跨平台差异集中在这一个小文件，主体配置两边永远相同 |
| `switch-pi-agent.py` | 主脚本。扫描进程表找到所有运行中的 Pi Agent，定位其所在的 Kitty Tab / Byobu 窗格与工作目录，用 `fzf` 交互选择后跳转（按 Kitty Tab 分组展示；子行的 tmux 状态栏信息与底部 tab 栏逐格对齐；✅ 子行在名称后附完成时刻，头部显示本次弹窗的加载耗时） |
| `kitty-tab-sync.ts` | Pi 扩展。综合 `agent_start` / `agent_settled`、阻塞式 UI prompt 与 watchdog 生命周期，用 pane 级 `@pi_running` / `@pi_done`（并记录完成时刻 `@pi_done_at`）作事实源，写逐窗口 `@pi_win`（tmux 窗口栏 ⏳ 运行中 / ✅ 已完成待关注）与会话级 `@pi_total`（kitty 标题 ⏳N ✅M）；每次状态变化 5 秒后用零宽 `@pi_nonce` 再推一次标题，兜住「断网正好卡在推送那一刻」；跑完发 BEL 让 kitty 的 Dock 图标跳动 |
| `tmux-pane-command.py` | tmux 状态栏 helper。当 `pane_current_command` 只能看到沙盒 wrapper `enclave` 时，从前台 leader 的完整 `enclave run ...` 启动命令直接提取真实应用名 |
| `tmux-pane-repo.py` | tmux 状态栏 helper。用 git 求出 pane 工作目录的 `<项目名>/<位置>`：主仓为 `main`，linked worktree 为 `wt:<worktree 名>`，非 git 目录输出空串。一次调用同时供 `@pi_repo` 与 `@pi_where`，也是 picker 侧同一事实源（`#()` 在 `list-panes` 里不执行，只能另调） |
| `tmux-pi-ack.py` | tmux 状态栏 helper（`after-select-window` / `after-select-pane` hook，以及 `MouseDown1Status` 绑定调用）。切到或点中某个 window 时把该 window 的 ✅（done-unseen）清成已读，并立即重算 `@pi_win` / `@pi_total`；负责「看一眼即已读」的即时生效 |
| `tmux.linux.conf` | Linux 专属配置。`bind v` 不再是 Mac 的 `open -a`，改调 `tmux-open-vscode.py`：先试 SSH RemoteForward 隧道（Mac 监听器收到路径后 `open` 一个 `vscode://vscode-remote/...` 窗口），隧道不通则降级为 OSC 52 把 URL 写进 Mac 剪贴板；两种失败都向 stderr 说明具体原因 |
| `tmux-open-vscode.py` | `bind v` 的 helper（VPS 侧）。双路线自动切换 + 失败原因可见：隧道拒绝/挂起/不通分别打印监听器回复、超时信息、死 socket 原因。**每连接一个独占 socket**：glob `/tmp/vscode-open-remote-*` 后按 mtime 新到旧**直接试**，第一个回 `OK` 的赢了就收工；全部不行才降级 OSC 52。故意不做预探活：死文件在 connect 时本来就秒拒，预探只会让每个候选多开一条穿透到 Mac 的无效连接。监听器明确回 `rejected` 时立刻停止——所有隧道都通到同一个 Mac 监听器，换一条只会得到同一句话。整轮 15 秒预算（`bind v` 用 `run-shell -b`，不阻塞界面），单次 `REPLY_TIMEOUT` 2.5 秒——实测这条链路健康往返 p50 330ms / p90 1.0s / 最差 1.8s，定成 1 秒会把健康隧道误判成死的。`--probe`：发一个**不在白名单里**的 URL，监听器必然回 `rejected`，于是只证明「socket + 隧道 + 监听器」全通则一个窗口都不弹；`verify-kitty-tunnel.sh` 就靠它体检 |
| `scripts/vscode-open-listener.py` | Mac 侧监听器（launchd 常驻，127.0.0.1:19998）。收 VPS 经隧道发来的 `vscode://vscode-remote/ssh-remote+vps/...` URL，校验白名单前缀后交给 `open` 弹出 Remote 窗口；只 `open`，不执行任意命令。**每连接一个线程 + 先回 `OK` 再 `open`**：`open` 冷启动要几秒，单线程时下一个人拿不到 ack，而 VPS 侧 helper 把沉默当死隧道。线程化之后 ack 只花一个 RTT，同时避开了「有人连上不发数据就把监听器卡死」（`recv` 也加了 10 秒超时）。`accept` 遇到 EMFILE 这类瞬时错误只退避不退出。**没做 URL 去重**：去重会误伤「同个项目开了两个 tab、10 秒内各按一次」，而重复弹窗只是外观问题 |
| `scripts/tssh-vps.sh` | Mac 侧 `tssh vps` 的替代品（`deploy.sh` 软链到 `~/.local/bin/tssh-vps`）。kitty `listen_on` 的 socket 名一定含 PID（重启就变），静态 RemoteForward 会指向死 socket，wrapper 在进门前探活拿到活 socket 后 `exec tssh -R /tmp/mykitty-remote-<pid>-<随机>:<活 socket> vps`；**每条连接用独占路径**——共用固定名时，后连上来的必须先把旧路径 unlink 才能绑，老连接的 listener 还挂在内核里却永远连不上，而最后一条断开时 OpenSSH 收回属于自己的文件，路径就彻底消失，picker 只剩「No Kitty Tab」；独占路径下重连到同一个 tsshd 还能找回自己那份文件；socket 发现：`$KITTY_LISTEN_ON` 优先，`ls -t /tmp/mykitty-*` 逐个探活兑底；找不到时降级为无分组的 tssh，不阻塞。同一连接顺带转发 VS Code 隧道：**`-R /tmp/vscode-open-remote-<pid>-<随机>:127.0.0.1:19998`**，即 VPS 上的 unix socket 反代到 Mac 的监听器（`vscode-open-listener.py`）。这里以前是写死端口的 `-R 19998:127.0.0.1:19998`，而**端口是全局唯一资源**：谁先连上谁绑住，后来的全部 EADDRINUSE；tssh 不实现 `ExitOnForwardFailure`（0.1.27 源码里根本没这个选项），只打一行 warning 照常进门，于是**每个输家都以为自己有隧道**，赢家那条传输一断，任何 tab 按 `bind v` 都是往虚空里写。改成每连接独占路径后抢端口的死局消失。tssh 支持 `/remote_socket:host:port` 这个形状（`forward.go` 的 `parseForwardArg`，路径必须是绝对路径）；代价是 `~/.ssh/config` 里 `Host vps` 的旧 `RemoteForward 19998` 行必须删掉，否则端口又被拖回争抢。注意：独占路径只是让一条坏掉不连累其他，**roam 本身修不了**——tssh 只在登录时发一次转发请求，断网重连不会重建。隧道随连接生灭，无需 autossh 与清理；VPS 侧零改动 |
| `scripts/com.pi-kitmux.vscode-open-listener.plist` | 上一条的 launchd 配置（KeepAlive + RunAtLoad），`deploy.sh` 在 macOS 上自动安装并加载 |
| `scripts/check.sh` | 一键验证：pre-commit 静态检查（ruff / codespell / vulture / mypy / pyright / pylint）+ `tests/` 的 Python 单测 + `tests/test_title_resend.mjs` 的 node 行为测试 |
| `scripts/verify-kitty-tunnel.sh` | 隧道体检。`--now` 检查当前每条 tsshd 连接：fd 1 取下 socket inode，到 `/proc/net/unix` 反查路径，再判断文件是否在、kitty 是否能应答、tab 标签能否解码；并比对「连接数 vs picker 能摸到的路径数」，少一个就说明有连接被共用路径遮住了。顺带体检 `bind v` 那条：统计 `/tmp/vscode-open-remote-*` 数量和活监听器，再调 helper 的 `--probe` 走一遍完整链路——这一项**故意不计入退出码**，Mac 上没装监听器跟 kitty 隧道坏没坏是两回事，否则每次都 FAIL 会让人学会忽略报告。不带参数则等下一次连接（断线重连或新开），验完给出 PASS/FAIL。`tssh` 不认 `ExitOnForwardFailure`，转发失败只打 warning 照常进门，所以这个脚本是唯一能确认隧道真的通了的手段 |
| `scripts/deploy.sh` | 部署：把 `tmux.conf` 软链到 `~/.tmux.conf` 与 `~/.byobu/keybindings.tmux`，平台配置软链到 `~/.tmux.darwin.conf` / `~/.tmux.linux.conf`，五个 tmux helper 软链到 `~/.local/bin/`，Pi 扩展软链到 `~/.pi/agent/extensions/`；macOS 上另装 VS Code 监听器的 launchd 服务。首次运行会把已存在的真实文件备份为 `*.bak.<时间戳>` |
| `tests/` | Python helper 的单元测试（含 Mac 侧监听器：白名单、NUL、ack 先于 `open`、慢 `open` 不阻塞下一个连接），外加 `test_tmux_conf.py`（钉住 `set-titles-string` 里的零宽 nonce 契约）与 `test_title_resend.mjs`（node 测试：起临时 tmux server 挂 pty，数真实推出去的标题 OSC，验补推行为） |

## 工作原理

`switch-pi-agent.py`（纯标准库，无第三方依赖）：

1. **进程发现**：单次 `ps axo pid,ppid,command` 构建进程表，按命令行特征（`pi` / `pi-coding-agent`）筛选出 Pi Agent 进程。
2. **位置映射**：
   - Kitty：`kitty @ ls` 列出所有 tab 及其 pane 的 pid；tab 标题按 `kitty.conf` 的 `tab_title_template` 动态渲染，与 tab bar 显示一致
   - Byobu/tmux：`tmux list-panes -a` 按 `pane_pid` 索引，结合祖先链把 Agent 映射到 session / window / pane
   - 工作目录：对匹配到的少量 pid 用 `lsof -d cwd` 查询
3. **选择跳转**：`fzf` 列表按 Kitty Tab 分组，组头是「tab 标题 · session」，组内每个 agent 一行；子行的 tmux 部分与状态栏逐格对齐 ——「`窗口号: ⏳/✅ 名称@位置`」，其中 `⏳/✅`（pane 级 `@pi_running` / `@pi_done`）取自 tmux，✅ 行末尾另附 `(完成时刻)`（pane 级 `@pi_done_at` 的 epoch 秒，同日显示 `HH:MM:SS`、跨日显示 `MM-DD HH:MM`），`@位置` 与项目名由 `tmux-pane-repo.py` 现调（与状态栏同一事实源），enclave 窗口缺失的应用名由 `tmux-pane-command.py` 现补，故不会与 tab 栏漂移；session 已上移到父节点，子行按窗口号排序，顺序也与 tab 栏一致。选中组头只聚焦该 tab，选中子行则同时切到具体 session/window/pane（只动目标 client，不会误动当前 tab 自己的 client）。无 Kitty Tab 的兜底组仍在子行保留 session 名。头部显示全局「⏳N 运行中 · ✅M 待关注」计数，以及本次弹窗的加载耗时（进程启动到 `fzf` 弹出之间的收集耗时）。

安全细节：

- kitty socket 自动发现（`KITTY_LISTEN_ON` > `/tmp/mykitty-*` 最新 > 默认探测），避免连死 socket 挂起；候选先只收真 socket（`S_ISSOCK`），再对每个文件做一次 microsecond 级的 `connect` 探活，连不上就跳过——连接结束后会留下一个死文件，越积越多，而**活隧道往往是最老的那个**（长连接），所以任何「按个数/按 mtime 截断」都会把活隧道漏掉、又变回「No Kitty Tab」；真正区分死活的是能不能连上，不是新旧
- 探活另有一个 5 秒的总预算（`_PROBE_BUDGET`），平时用不到（上面那道 connect 已经刷掉死文件），只防「连得上但不说协议」的怪 socket 把弹窗拖死
- 判定「这条 socket 能用」要求 `kitty @ ls` 返回合法 JSON；以前只要 stdout 非空就当成功，会把只吐错误信息的死 socket 缓存下来，之后 `focus-tab` 全打空
- **绝不删 socket 文件**：删掉一个 socket 文件会让它背后的隧道彻底失联，这正是要修的那个 bug；残留文件靠上面的 socket 过滤兜底
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
- 标题补发：tmux 只在**渲染出的字符串变化**时推 OSC，推过就记进 client 缓存。断网正好卡在推的那一瞬，OSC 丢了而缓存已是新值，重连后 tmux 认为「没变化」，kitty 的 tab 永远停在旧状态。所以每次状态变化后再推一次：`set-titles-string` 末尾挂一个 session 级 `@pi_nonce`（一个零宽字符，tab 上不可见、picker 解 tag 时被忽略），5 秒后翻成另一个——字符串变了，tmux 必须重推。网络正常时这是一次多余的重复；断网时这一份会落进 tsshd 的断线缓存（它 3 秒收不到心跳后改把 PTY 输出存内存，上限 1000 行，重连时一起 flush），于是跟着重连补出来。不用轮询、空闲零流量、状态不变就一个字节都不发
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

`scripts/check.sh` 跑三样：pre-commit 静态检查、`tests/` 下的 Python 单测、以及 `tests/test_title_resend.mjs`（node 测试）。最后一组是唯一能验到 `kitty-tab-sync.ts` 行为的手段：它起一个用完就丢的 tmux server、挂一个 pty 客户端，直接数 tmux 推出去的标题 OSC，断言「状态变化后 5 秒有一次补推、可见文字不变、空闲零推送、退出时补推、连续变化只补一次」。单独跑：

```bash
node --experimental-strip-types --test tests/test_title_resend.mjs
```
