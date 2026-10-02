import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import * as path from "node:path";

// 状态分四个去处：
//   - pane 级 @pi_running：事实源（本 pane 的任务是否仍未完成；含 watchdog 续跑），pane 销毁自动清除。
//   - pane 级 @pi_done：本 pane 上一轮已真正跑完且未被新一轮覆盖，pane 销毁自动清除。
//   - window 级 @pi_win："⏳ "（有 pane 在跑，优先）/ "✅ "（全部跑完）/ ""，tmux/byobu 窗口栏逐窗口展示。
//   - session 级 @pi_total："⏳ N "（本 session 未完成的任务总数），kitty tab 标题用。
//     放 session 级是关键：新开的 tmux 窗口不需要等下一次广播就能在标题里看到总数。
// 跑完时向 tty 发 BEL：kitty 的 bell_on_tab 会给「未聚焦窗口」的 tab 加铃铛（响铃模式）。
// 已知边缘情形（SIGKILL / move-pane 跨 session）计数暂时偏差，但下次任意事件重算即自愈。
//
// 所有对 tmux 的「写」都经由 StatusSink：本机由 TmuxStatusSink 用 CLI 实现。
// 未来若 pi 跑在远端（VPS），只需换一个 sink 实现（例如把事件发往 SSH 反向 socket），
// 状态判定逻辑不必改动。读操作（display-message / list-panes）目前仍是本机 tmux 专属。
type WindowStatus = "running" | "done" | "idle";

interface StatusSink {
  setPaneRunning(on: boolean): void;
  setPaneDone(on: boolean): void;
  setWindowStatus(windowId: string, status: WindowStatus): void;
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

  // 与 @pi_running 同理，pane 级、pane 销毁自动清除；跑完置 1，下一轮/退出清空
  setPaneDone(on: boolean): void {
    this.cmd(["set", "-pq", "-t", this.pane, "@pi_done", on ? "1" : ""]);
  }

  // 每个窗口一个展示态：running 优先，其次 done，否则空
  setWindowStatus(windowId: string, status: WindowStatus): void {
    const text = status === "running" ? "⏳ " : status === "done" ? "✅ " : "";
    this.cmd(["set", "-wq", "-t", windowId, "@pi_win", text]);
  }

  // 会话级总数：kitty tab 标题用；放 session 级，新开的 tmux 窗口无需等广播即能显示
  setSessionTotal(sessionId: string, text: string): void {
    this.cmd(["set", "-t", sessionId, "@pi_total", text]);
  }

  // 防御性清理：废弃的 window 级 @pi_status / @pi_done 全局默认值清掉。
  // 注意：这里清的是「window 级」遗留的 @pi_done；本扩展现用的 @pi_done 是 pane 级
  // （set -pq），命名空间不同，别把这里的清理误当成在删新功能。
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
// watchdog 自身的监控状态（含倒计时/输入暂停）；由 pi.events 跨扩展同步。
// 注意这是「watchdog 的状态」而非「agent 的状态」：空会话里它会自行 armed，
// 因此只在下面 hasRunOnce 为真时，才把它当作「settled 后仍会续跑」的抑制项。
let watchdogActive = false;
// watchdog 广播的「上一轮被用户按 Esc 打断」：此时它仍 armed（running=true），
// 但本次空闲不会再续跑，所以不能算作「settled 后仍会续跑」的抑制项，
// 也不能把这次结束当成「真正跑完」。只有用户发下一条真实消息时 watchdog 才会清回 false。
let watchdogInterrupted = false;
// 本进程是否已经跑过至少一轮。用于把 watchdogActive 收窄到真正有活可续的场景，
// 否则新开/恢复的空会话会因为 watchdog 自启动而误亮 ⏳。session_start 时复位。
let hasRunOnce = false;
// 阻塞式 UI prompt 期间已临时置 idle 的标志（让重复 start/end 幂等）。
let pausedByPrompt = false;
// 已实际写入 tmux/标题的 effective 状态，避免重复写与重复响铃。
let isMarkedRunning = false;
// pi 进程正在退出（cleanupOnExit）：退出不是「跑完」，不得置 ✅，且需强制清掉旧的 ✅。
let quitting = false;
// session_start 期间的状态校正：pane 残留的 @pi_running 被清掉不算「跑完」，抑制误置 ✅。
let suppressDone = false;

function getSessionId(): string | null {
  try {
    return execFileSync(
      "tmux", ["display-message", "-p", "-t", process.env.TMUX_PANE!, "#{session_id}"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).trim() || null;
  } catch { return null; }
}

// 读本 pane 的事実源（供启动/重载时恢复内存状态）
function readPaneRunning(): boolean {
  const pane = process.env.TMUX_PANE;
  if (!(process.env.TMUX && pane)) return false;
  try {
    // -q：pane 上首次还没设过 @pi_running 时 tmux 会向 stderr 打 "invalid option"，
    // 默认 stdio 会把它漏进 pi 的 TUI；-q + 吞掉 stderr 双保险。
    return execFileSync(
      "tmux", ["show-options", "-pvq", "-t", pane, "@pi_running"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).trim() === "1";
  } catch { return false; }
}

// 一次 list-panes 同时算出「每个窗口跑没跑」和「本 session 运行中总数」：
//   @pi_win   = "⏳ " / "✅ " / ""（window 级，tmux 窗口栏逐窗口；running 优先于 done）
//   @pi_total = "⏳ N "（session 级，kitty tab 标题；放 session 级后新窗口自动继承）
// 并发写为 last-writer-wins，偏差窗口毫秒级，下次任意事件自愈。
function broadcastStatus() {
  const pane = process.env.TMUX_PANE;
  const isTmux = Boolean(process.env.TMUX && pane);
  if (!isTmux) return;

  try {
    const sessionId = getSessionId();
    if (!sessionId) return;
    // 逐窗口聚合三态：只要有 pane 在跑就是 running；否则只要有 pane done 就是 done
    const perWindow = new Map<string, WindowStatus>();
    for (const line of execFileSync(
      "tmux", ["list-panes", "-s", "-t", sessionId, "-F", "#{window_id} #{@pi_running} #{@pi_done}"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).split("\n")) {
      const [windowId, runningFlag, doneFlag] = line.trim().split(/\s+/);
      if (!windowId) continue;
      const current = perWindow.get(windowId);
      if (runningFlag === "1") perWindow.set(windowId, "running");
      else if (doneFlag === "1" && current !== "running") perWindow.set(windowId, "done");
      else if (!current) perWindow.set(windowId, "idle");
    }

    const total = [...perWindow.values()].filter((status) => status === "running").length;
    sink.setSessionTotal(sessionId, total > 0 ? `⏳ ${total} ` : "");
    for (const [windowId, status] of perWindow) {
      sink.setWindowStatus(windowId, status);
    }
    sink.refresh();
  } catch {}
}

function updateStatus(running: boolean, done: boolean) {
  const isTmux = Boolean(process.env.TMUX);

  if (isTmux) {
    // 自身标记：pane 级选项，pane 销毁时自动清除，不会残留
    sink.setPaneRunning(running);
    sink.setPaneDone(done);
    // 防御性清理：旧版扩展用的 window 级 @pi_* 已废弃
    sink.clearLegacy();
    broadcastStatus();
  } else {
    // 兼容非 tmux 环境
    const folder = path.basename(process.cwd());
    const statusTag = running ? "⏳ " : done ? "✅ " : "";
    process.stdout.write(`\x1b]2;${statusTag}${folder}\x07`);
  }

  // 跑完响铃：kitty 的 bell_on_tab 会给未聚焦窗口的 tab 加铃铛（响铃模式）。
  // 以 done 为准而非 !running：退出(quitting)、session_start 校正、UI prompt 暂停、
  // 用户 Esc 打断都不是「跑完」，都不该响铃。
  if (done) process.stdout.write("\x07");
}

function applyEffectiveState() {
  // UI prompt 表示 AI 正在等人，优先。
  // watchdogActive 只在已经跑过一轮后生效：那时它代表「本轮 settled 后 watchdog 还会续跑」；
  // 跑过之前它只说明 watchdog 自我 armed（空会话/恢复会话），与 agent 是否在跑无关。
  const running = !pausedByPrompt && (agentRunning || (watchdogActive && hasRunOnce && !watchdogInterrupted));
  if (running === isMarkedRunning) return;
  isMarkedRunning = running;
  // 只有「真正跑完」才置 ✅：等待用户 prompt、pi 退出、session_start 的状态校正，
  // 以及用户按 Esc 打断（watchdog 不再续跑、这一轮并未真正跑完）都不是完成，均抑制。
  const done = !running && !pausedByPrompt && !quitting && !suppressDone && !watchdogInterrupted;
  updateStatus(running, done);
}

function cleanupOnExit() {
  quitting = true;
  agentRunning = false;
  watchdogActive = false;
  pausedByPrompt = false;
  const wasRunning = isMarkedRunning;
  isMarkedRunning = false;
  if (wasRunning) {
    // 正常 running→false：走常规路径清掉 pane 标记；@pi_done 因 quitting 保持空，故不响铃
    updateStatus(false, false);
  } else if (process.env.TMUX && process.env.TMUX_PANE) {
    // 已经处于 done/idle：applyEffectiveState 会 early-return，必须强制清掉
    // pane 上的 @pi_done，避免 ✅ 留在已退出的 pane 上。
    sink.setPaneRunning(false);
    sink.setPaneDone(false);
    broadcastStatus();
  }
}

export default function (pi: ExtensionAPI) {
  // watchdog 是独立扩展：它在 agent_settled 后倒计时，再用 sendUserMessage 开新一轮。
  // 这里同步的是「watchdog 自己的状态」，不是 agent 状态：只有它知道本轮 settled 后
  // 是否还会续跑，所以仅用来抑制提前判定完成/响铃（是否生效见 hasRunOnce 门控）。
  pi.events.on("watchdog:state", (raw: unknown) => {
    const state = raw as { running?: unknown; interrupted?: unknown } | undefined;
    watchdogActive = Boolean(state?.running);
    watchdogInterrupted = Boolean(state?.interrupted);
    applyEffectiveState();
  });

  // 启动/热重载时从 pane 事实源恢复已写状态并重算一次；随后查询 watchdog 当前状态，
  // 避免扩展加载顺序或 reload 导致漏掉它先前发出的广播。
  pi.on("session_start" as any, async (event: any, ctx: any) => {
    // /new、/resume、/fork 是「换了一个 session」，上一轮的 ✅ 属于旧上下文，清掉；
    // reload（热重载）和 startup（新进程，正常退出已清过）保留 pane 上的 ✅。
    if (event?.reason !== "reload" && process.env.TMUX && process.env.TMUX_PANE) {
      sink.setPaneDone(false);
    }
    // 状态校正期间禁止置 ✅：reload 后残留的 @pi_running=1 被清掉是「校正」而非「跑完」
    suppressDone = true;
    // pane 事实源可能是残留（SIGKILL 等），也可能真是「热重载时 agent 正跑着」——
    // 两者都表现为 @pi_running=1。再问一次会话是否真的忙（ctx.isIdle），同为真才认定
    // 本轮在跑，避免把残留当成运行中；isIdle 不可用时保守按否处理。
    const factRunning = readPaneRunning();
    const busy = factRunning && ctx?.isIdle?.() === false;
    agentRunning = busy;
    hasRunOnce = busy;
    watchdogActive = false;
    watchdogInterrupted = false;
    pausedByPrompt = false;
    // 保留 pane 事实源不变，交由下方 setTimeout 的校正路径决定是否清除
    isMarkedRunning = factRunning;
    // 直接读 pane 选项：@pi_done=1 的窗口立即恢复 ✅，且不受其它 session_start 影响
    broadcastStatus();
    // 延后一拍，让所有扩展的 session_start handler 先跑完：若 watchdog 会按 env
    // 自动启动，它会先广播 true；随后 query 再确认最终状态。若没有 watchdog，
    // query 无响应，apply 会清掉 reload/异常退出留下的旧 pane 标记。
    setTimeout(() => {
      watchdogActive = false;
      watchdogInterrupted = false;
      pi.events.emit("watchdog:state:query");
      applyEffectiveState();
      suppressDone = false; // 必须在 apply 之后复位，否则校正路径可能误置 ✅
    }, 0);
  });

  pi.on("agent_start" as any, async () => {
    hasRunOnce = true;
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
    // 新的交互（prompt）已开始，上一轮的 ✅ 不再代表当前状态；纯 done→prompt 时
    // applyEffectiveState 会因 running 未变而 early-return，所以这里直接清并广播。
    if (process.env.TMUX && process.env.TMUX_PANE) sink.setPaneDone(false);
    applyEffectiveState();
    broadcastStatus();
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
