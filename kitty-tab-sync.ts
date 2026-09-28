import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import * as path from "node:path";

// 状态分三个去处：
//   - pane 级 @pi_running：事实源（本 pane 的任务是否仍未完成；含 watchdog 续跑），pane 销毁自动清除。
//   - window 级 @pi_win："⏳ " / ""，tmux/byobu 窗口栏每个 tab 只显示自己是否仍在处理（不带数字）。
//   - session 级 @pi_total："⏳ N "（本 session 未完成的任务总数），kitty tab 标题用。
//     放 session 级是关键：新开的 tmux 窗口不需要等下一次广播就能在标题里看到总数。
// 跑完时向 tty 发 BEL：kitty 的 bell_on_tab 会给「未聚焦窗口」的 tab 加铃铛（响铃模式）。
// 已知边缘情形（SIGKILL / move-pane 跨 session）计数暂时偏差，但下次任意事件重算即自愈。
//
// 所有对 tmux 的「写」都经由 StatusSink：本机由 TmuxStatusSink 用 CLI 实现。
// 未来若 pi 跑在远端（VPS），只需换一个 sink 实现（例如把事件发往 SSH 反向 socket），
// 状态判定逻辑不必改动。读操作（display-message / list-panes）目前仍是本机 tmux 专属。
interface StatusSink {
  setPaneRunning(on: boolean): void;
  setWindowRunning(windowId: string, on: boolean): void;
  setSessionTotal(sessionId: string, text: string): void;
  clearLegacy(): void;
  refresh(): void;
}

class TmuxStatusSink implements StatusSink {
  private readonly pane: string;

  constructor(pane: string) {
    this.pane = pane;
  }

  private cmd(args: string[]): void {
    try { execFileSync("tmux", args, { stdio: "ignore" }); } catch {}
  }

  // pane 级选项，pane 销毁时自动清除，不会残留
  setPaneRunning(on: boolean): void {
    this.cmd(["set", "-pq", "-t", this.pane, "@pi_running", on ? "1" : ""]);
  }

  // 每个窗口只标记「跑没跑」，不带数字
  setWindowRunning(windowId: string, on: boolean): void {
    this.cmd(["set", "-wq", "-t", windowId, "@pi_win", on ? "⏳ " : ""]);
  }

  // 会话级总数：kitty tab 标题用；放 session 级，新开的 tmux 窗口无需等广播即能显示
  setSessionTotal(sessionId: string, text: string): void {
    this.cmd(["set", "-t", sessionId, "@pi_total", text]);
  }

  // 防御性清理：废弃的 window 级 @pi_status / @pi_done 全局默认值清掉
  clearLegacy(): void {
    this.cmd(["set", "-gu", "@pi_status"]);
    this.cmd(["set", "-wgu", "@pi_status"]);
    this.cmd(["set", "-wgu", "@pi_done"]);
  }

  // 立即刷新状态栏（否则要等 status-interval 才更新）
  refresh(): void {
    this.cmd(["refresh-client", "-S"]);
  }
}

const sink: StatusSink = new TmuxStatusSink(process.env.TMUX_PANE ?? "");

// Pi 当前这一轮是否在执行。agent_settled 只结束这一轮，不代表 watchdog 不会稍后续跑。
let agentRunning = false;
// watchdog 是否仍处于监控状态（含倒计时/输入暂停）；由 pi.events 跨扩展同步。
let watchdogActive = false;
// 阻塞式 UI prompt 期间已临时置 idle 的标志（让重复 start/end 幂等）。
let pausedByPrompt = false;
// 已实际写入 tmux/标题的 effective 状态，避免重复写与重复响铃。
let isMarkedRunning = false;

function getSessionId(): string | null {
  try {
    return execFileSync(
      "tmux", ["display-message", "-p", "-t", process.env.TMUX_PANE!, "#{session_id}"],
      { encoding: "utf8" }
    ).trim() || null;
  } catch { return null; }
}

// 读本 pane 的事実源（供启动/重载时恢复内存状态）
function readPaneRunning(): boolean {
  const pane = process.env.TMUX_PANE;
  if (!(process.env.TMUX && pane)) return false;
  try {
    return execFileSync(
      "tmux", ["show-options", "-pv", "-t", pane, "@pi_running"],
      { encoding: "utf8" }
    ).trim() === "1";
  } catch { return false; }
}

// 一次 list-panes 同时算出「每个窗口跑没跑」和「本 session 运行中总数」：
//   @pi_win   = "⏳ " / ""（window 级，tmux 窗口栏逐窗口）
//   @pi_total = "⏳ N "（session 级，kitty tab 标题；放 session 级后新窗口自动继承）
// 并发写为 last-writer-wins，偏差窗口毫秒级，下次任意事件自愈。
function broadcastStatus() {
  const pane = process.env.TMUX_PANE;
  const isTmux = Boolean(process.env.TMUX && pane);
  if (!isTmux) return;

  try {
    const sessionId = getSessionId();
    if (!sessionId) return;
    const perWindow = new Map<string, boolean>();
    for (const line of execFileSync(
      "tmux", ["list-panes", "-s", "-t", sessionId, "-F", "#{window_id} #{@pi_running}"],
      { encoding: "utf8" }
    ).split("\n")) {
      const [windowId, flag] = line.trim().split(/\s+/);
      if (!windowId) continue;
      if (flag === "1") perWindow.set(windowId, true);
      else if (!perWindow.has(windowId)) perWindow.set(windowId, false);
    }

    const total = [...perWindow.values()].filter(Boolean).length;
    sink.setSessionTotal(sessionId, total > 0 ? `⏳ ${total} ` : "");
    for (const [windowId, running] of perWindow) {
      sink.setWindowRunning(windowId, running);
    }
    sink.refresh();
  } catch {}
}

function updateStatus(running: boolean) {
  const isTmux = Boolean(process.env.TMUX);

  if (isTmux) {
    // 自身标记：pane 级选项，pane 销毁时自动清除，不会残留
    sink.setPaneRunning(running);
    // 防御性清理：旧版扩展用的 window 级 @pi_* 已废弃
    sink.clearLegacy();
    broadcastStatus();
  } else {
    // 兼容非 tmux 环境
    const folder = path.basename(process.cwd());
    const statusTag = running ? "⏳ " : "";
    process.stdout.write(`\x1b]2;${statusTag}${folder}\x07`);
  }

  // 跑完响铃：kitty 的 bell_on_tab 会给未聚焦窗口的 tab 加铃铛（响铃模式）
  if (!running) process.stdout.write("\x07");
}

function applyEffectiveState() {
  // UI prompt 表示 AI 正在等人，优先于 agent/watchdog 的 running 状态。
  const running = !pausedByPrompt && (agentRunning || watchdogActive);
  if (running === isMarkedRunning) return;
  isMarkedRunning = running;
  updateStatus(running);
}

function cleanupOnExit() {
  agentRunning = false;
  watchdogActive = false;
  pausedByPrompt = false;
  applyEffectiveState();
}

export default function (pi: ExtensionAPI) {
  // watchdog 是独立扩展：它在 agent_settled 后倒计时，再用 sendUserMessage
  // 开新一轮。只有 watchdog 自己知道是否还会续跑，因此通过共享事件总线同步真值。
  pi.events.on("watchdog:state", (raw: unknown) => {
    watchdogActive = Boolean((raw as { running?: unknown } | undefined)?.running);
    applyEffectiveState();
  });

  // 启动/热重载时从 pane 事实源恢复已写状态并重算一次；随后查询 watchdog 当前状态，
  // 避免扩展加载顺序或 reload 导致漏掉它先前发出的广播。
  pi.on("session_start" as any, async () => {
    agentRunning = false;
    watchdogActive = false;
    pausedByPrompt = false;
    isMarkedRunning = readPaneRunning();
    broadcastStatus();
    // 延后一拍，让所有扩展的 session_start handler 先跑完：若 watchdog 会按 env
    // 自动启动，它会先广播 true；随后 query 再确认最终状态。若没有 watchdog，
    // query 无响应，apply 会清掉 reload/异常退出留下的旧 pane 标记。
    setTimeout(() => {
      watchdogActive = false;
      pi.events.emit("watchdog:state:query");
      applyEffectiveState();
    }, 0);
  });

  pi.on("agent_start" as any, async () => {
    agentRunning = true;
    applyEffectiveState();
  });

  // agent_settled 只表示 Pi 当前一轮已完全 settle；若 watchdog 仍 active，
  // 它还会在倒计时后发消息续跑，所以此时保持 running，不提前响铃。
  pi.on("agent_settled" as any, async () => {
    agentRunning = false;
    applyEffectiveState();
  });

  // 阻塞式 UI prompt（plan 评审的 select/editor 等）期间语义上是等待用户，
  // 即使 agent/watchdog 尚未结束，也暂时收掉 ⏳；prompt 结束后按真实状态恢复。
  pi.on("ui_prompt_start" as any, async () => {
    if (pausedByPrompt) return;
    pausedByPrompt = true;
    applyEffectiveState();
  });
  pi.on("ui_prompt_end" as any, async (_event: any, ctx: any) => {
    if (!pausedByPrompt) return;
    pausedByPrompt = false;
    // 弹窗期间本轮若已真正结束（例如被 abort），同步修正 agentRunning。
    if (ctx?.isIdle?.()) agentRunning = false;
    applyEffectiveState();
  });

  process.on("exit", cleanupOnExit);
  process.on("SIGINT", () => {
    cleanupOnExit();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    cleanupOnExit();
    process.exit(0);
  });
  // kill-pane / 关 tab / 关窗口会发 SIGHUP，不清会导致窗口计数残留偏高
  process.on("SIGHUP", () => {
    cleanupOnExit();
    process.exit(0);
  });
}
