---
description: 用 playwright-cli 连接本机真实 Chrome（扩展桥接），按步骤执行浏览器操作
argument-hint: "<要执行的步骤>"
---
# 目标
使用 `playwright-cli`（已安装）控制本机正在运行的真实 Chrome，执行以下步骤：

$ARGUMENTS

# 连接方式（先做这一步）
1. 先从 `~/.pi/chrome-extension-token.txt` 读取桥接 token（第一行即为 token），然后连接用户已打开的真实 Chrome：
   ```bash
   TOKEN=$(head -1 ~/.pi/chrome-extension-token.txt) && \
   PLAYWRIGHT_MCP_EXTENSION_TOKEN=$TOKEN playwright-cli attach --extension=chrome
   ```
   - 若报"连接对话框/需要确认"类错误，确认 token 变量确实带上了；若 token 失效，让用户更新上述文件内容。
   - 若提示未安装 "Playwright MCP Bridge" 扩展，告知用户在 Chrome 安装该扩展后重试。

## 连接故障排查（attach 挂起/超时时按顺序检查）

已踩过的坑：**残留的 playwright-cli 后台守护进程会导致新 attach 无限挂起**
（症状：attach 命令不返回、无输出、超时；`pgrep -fl cliDaemon` 能看到多个 cliDaemon.js 进程）。
排查顺序：
1. `pgrep -fl cliDaemon` 查残留守护进程；有则 `pkill -f cliDaemon` 清掉（只杀守护进程，**不碰用户 Chrome**），再重试 attach。
2. 同时有多个 `attach --extension` 进程在挂起等待时，先 `pkill -f "attach --extension"` 再重试。
3. 若清完守护后 attach 仍长时间无响应（>1 分钟），大概率是 **Chrome 端问题**：
   Playwright MCP Bridge 扩展没在监听/待确认/未启动。请用户手动检查 Chrome 扩展图标状态，
   必要时用 `open -a "Google Chrome"`（macOS）拉起/激活 Chrome 后再重试
   （实测：Chrome 未运行导致 attach 挂起，agent 自行 `open -a "Google Chrome"` 后 attach 立即成功；
   可用 `pgrep -x "Google Chrome"` 检查 Chrome 是否在运行）。
4. attach 可能需要较长时间才返回，前台等待超时时可改后台执行：
   ```bash
   TOKEN=$(head -1 ~/.pi/chrome-extension-token.txt)
   PLAYWRIGHT_MCP_EXTENSION_TOKEN=$TOKEN nohup playwright-cli attach --extension=chrome > /tmp/attach.log 2>&1 &
   ```
   然后轮询 `cat /tmp/attach.log` 和 `playwright-cli -s=chrome snapshot` 确认是否连上。

2. 连接成功后用 `playwright-cli snapshot` 确认页面状态，从 snapshot 的元素 ref 出发操作。

# 操作规则
- 每次 `click`/`fill` 等操作前先 `snapshot`（或 `find <文本>`）拿到元素 ref；优先用 `find` 定位以省 token。
- 导航用 `goto <url>`；输入用 `fill <ref> <text>`；按键用 `press <key>`。
- 需要看页面内容时用 `snapshot --depth=N` 限制深度；需要截图验证时用 `screenshot --filename=...`。
- 不要关闭用户的 Chrome：结束后如需断开，用 `playwright-cli detach`，绝不用 `kill-all`/`close` 关浏览器本身。
- 每步执行后把结果简要汇报给用户。

# 现在开始
按上述步骤执行，遇到歧义先询问。
