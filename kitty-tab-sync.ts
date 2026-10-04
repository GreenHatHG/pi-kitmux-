import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import * as path from "node:path";

// Status lives in four places:
//   - pane-level @pi_running: the source of truth (is this pane's task still unfinished,
//     including a watchdog rerun); tmux clears it when the pane dies.
//   - pane-level @pi_done: this pane's last run really finished and was not covered by a
//     new run; tmux clears it when the pane dies.
//   - window-level @pi_win: "⏳ " (a pane is running, wins) / "✅ " (all done) / "", shown
//     per window in the tmux/byobu window bar.
//   - session-level @pi_total: "⏳N ✅M " (running / done-and-unseen task counts for this
//     session), used in the kitty tab title.
//     Session level matters: a newly opened tmux window shows the total without waiting
//     for the next broadcast.
//     ✅ means "finished but not looked at yet"; tmux-pi-ack.py clears it when you switch
//     to that window (the after-select-* hooks in tmux.conf).
// No BEL anymore: kitty's 🔔 is OS-window-wide, any program's bell sets it off, and it
// duplicates ✅M.
// Known edge cases (SIGKILL / move-pane across sessions) can skew counts for a while, but
// the next event recomputes and heals them.
//
// Every tmux write goes through StatusSink: locally TmuxStatusSink does it with the CLI.
// If pi ever runs on a remote box (VPS), just swap in another sink (e.g. send events over
// an SSH reverse socket); the status logic stays the same. Reads (display-message /
// list-panes) are still local-tmux only for now.
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

  // pane-level option; tmux clears it when the pane dies, so nothing lingers
  setPaneRunning(on: boolean): void {
    this.cmd(["set", "-pq", "-t", this.pane, "@pi_running", on ? "1" : ""]);
  }

  // Like @pi_running: pane-level, auto-cleared when the pane dies; 1 when done, cleared on the next run/exit
  setPaneDone(on: boolean): void {
    this.cmd(["set", "-pq", "-t", this.pane, "@pi_done", on ? "1" : ""]);
  }

  // One shown state per window: running first, then done, else empty
  setWindowStatus(windowId: string, status: WindowStatus): void {
    const text = status === "running" ? "⏳ " : status === "done" ? "✅ " : "";
    this.cmd(["set", "-wq", "-t", windowId, "@pi_win", text]);
  }

  // Session-level count for the kitty tab title (⏳ running / ✅ done-unseen); session level means a new tmux window shows it without a broadcast
  setSessionTotal(sessionId: string, text: string): void {
    this.cmd(["set", "-t", sessionId, "@pi_total", text]);
  }

  // Defensive cleanup: drop the old window-level @pi_status / @pi_done defaults.
  // Note: this clears the *window-level* leftover @pi_done; the @pi_done this
  // extension uses now is pane-level (set -pq), a different namespace, so don't
  // mistake this cleanup for deleting the new feature.
  clearLegacy(): void {
    this.cmd(["set", "-gu", "@pi_status"]);
    this.cmd(["set", "-wgu", "@pi_status"]);
    this.cmd(["set", "-wgu", "@pi_done"]);
  }

  // Refresh the status bar now (otherwise it waits for status-interval)
  refresh(): void {
    this.cmd(["refresh-client", "-S"]);
  }
}

const sink: StatusSink = new TmuxStatusSink(process.env.TMUX_PANE ?? "");

// Is Pi's current run in progress? agent_settled only ends this run; a watchdog may rerun later.
let agentRunning = false;
// watchdog's own state (countdown/input pause), synced across extensions via pi.events.
// Note this is the *watchdog's* state, not the agent's: it self-arms on an empty session,
// so only treat it as "will rerun after settle" when hasRunOnce is true below.
let watchdogActive = false;
// watchdog says the user pressed Esc on the last run: it stays armed (running=true), but
// this idle spell will not rerun, so it is not a "will rerun" suppressant and this end is
// not a real finish. Only a real new user message clears it back to false.
let watchdogInterrupted = false;
// Has this process run at least one run? Narrows watchdogActive to cases with real work to
// continue; otherwise a new/restored empty session would show ⏳ just from the watchdog
// self-starting. Reset on session_start.
let hasRunOnce = false;
// Set while a blocking UI prompt is open, so we temporarily show idle (makes repeat start/end idempotent).
let pausedByPrompt = false;
// The effective state actually written to tmux/title, to avoid double writes.
let isMarkedRunning = false;
// pi is exiting (cleanupOnExit): exit is not a finish, so never set ✅, and force-clear an old ✅.
let quitting = false;
// State fix-up during session_start: a leftover pane @pi_running being cleared is not a
// finish, so suppress a false ✅.
let suppressDone = false;

function getSessionId(): string | null {
  try {
    return execFileSync(
      "tmux", ["display-message", "-p", "-t", process.env.TMUX_PANE!, "#{session_id}"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).trim() || null;
  } catch { return null; }
}

// Read this pane's source of truth (to restore memory state on start/reload)
function readPaneRunning(): boolean {
  const pane = process.env.TMUX_PANE;
  if (!(process.env.TMUX && pane)) return false;
  try {
    // -q: on the first read, tmux prints "invalid option" to stderr for a pane with no
    // @pi_running yet, and the default stdio leaks it into pi's TUI; -q plus swallowing
    // stderr is double insurance.
    return execFileSync(
      "tmux", ["show-options", "-pvq", "-t", pane, "@pi_running"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).trim() === "1";
  } catch { return false; }
}

// Session total text, kept the same as session_total() in tmux-pi-ack.py (change one, change the other):
//   "⏳N ✅M ", drop a zero part, and "" when both are zero.
function sessionTotalText(running: number, done: number): string {
  const parts: string[] = [];
  if (running > 0) parts.push(`⏳${running}`);
  if (done > 0) parts.push(`✅${done}`);
  return parts.length > 0 ? `${parts.join(" ")} ` : "";
}

// One list-panes call computes both "did each window run" and "running / done-unseen
// counts for this session":
//   @pi_win   = "⏳ " / "✅ " / "" (window-level, tmux window bar; running beats done)
//   @pi_total = "⏳N ✅M " (session-level, kitty tab title; session level lets new windows inherit it)
// Concurrent writes are last-writer-wins; the skew lasts milliseconds and the next event heals it.
function broadcastStatus() {
  const pane = process.env.TMUX_PANE;
  const isTmux = Boolean(process.env.TMUX && pane);
  if (!isTmux) return;

  try {
    const sessionId = getSessionId();
    if (!sessionId) return;
    // Roll up three states per window: any running pane means running; otherwise any
    // done pane means done.
    // Split on "|", not spaces: an empty field (e.g. running empty, done=1) becomes
    // "@29  1" after a space join, and a /\s+/ split would shove done's value into
    // running's slot, reading "done" as "running" — the window would always be ⏳ and
    // @pi_total always too high. Splitting on the literal separator keeps empty fields.
    const perWindow = new Map<string, WindowStatus>();
    let runningTotal = 0;
    let doneTotal = 0;
    for (const line of execFileSync(
      "tmux", ["list-panes", "-s", "-t", sessionId, "-F", "#{window_id}|#{@pi_running}|#{@pi_done}"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }
    ).split("\n")) {
      const [windowId, runningFlag, doneFlag] = line.split("|");
      if (!windowId) continue;
      const running = runningFlag === "1";
      const done = doneFlag === "1";
      if (running) runningTotal += 1;
      if (done) doneTotal += 1;
      const current = perWindow.get(windowId);
      if (running) perWindow.set(windowId, "running");
      else if (done && current !== "running") perWindow.set(windowId, "done");
      else if (!current) perWindow.set(windowId, "idle");
    }

    sink.setSessionTotal(sessionId, sessionTotalText(runningTotal, doneTotal));
    for (const [windowId, status] of perWindow) {
      sink.setWindowStatus(windowId, status);
    }
    sink.refresh();
  } catch {}
}

function updateStatus(running: boolean, done: boolean) {
  const isTmux = Boolean(process.env.TMUX);

  if (isTmux) {
    // Our own marker: a pane-level option, auto-cleared when the pane dies.
    sink.setPaneRunning(running);
    sink.setPaneDone(done);
    // Defensive cleanup: the old window-level @pi_* are gone.
    sink.clearLegacy();
    broadcastStatus();
  } else {
    // Fallback for non-tmux setups
    const folder = path.basename(process.cwd());
    const statusTag = running ? "⏳ " : done ? "✅ " : "";
    process.stdout.write(`\x1b]2;${statusTag}${folder}\x07`);
  }
}

function applyEffectiveState() {
  // A UI prompt means the AI is waiting on a person, so it wins.
  // watchdogActive only counts after at least one run: then it means "the watchdog will
  // rerun after this run settles"; before that it only means the watchdog self-armed
  // (empty/restored session) and says nothing about the agent.
  const running = !pausedByPrompt && (agentRunning || (watchdogActive && hasRunOnce && !watchdogInterrupted));
  if (running === isMarkedRunning) return;
  isMarkedRunning = running;
  // Only a real finish gets ✅: waiting on a user prompt, pi exiting, session_start
  // fix-up, and a user Esc interrupt (watchdog will not rerun; the run did not really
  // finish) are all suppressed.
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
    // Normal running->false: use the usual path to clear the pane marker; @pi_done
    // stays empty because quitting is set.
    updateStatus(false, false);
  } else if (process.env.TMUX && process.env.TMUX_PANE) {
    // Already done/idle: applyEffectiveState would early-return, so force-clear @pi_done
    // on the pane, keeping a ✅ off an exited pane.
    sink.setPaneRunning(false);
    sink.setPaneDone(false);
    broadcastStatus();
  }
}

export default function (pi: ExtensionAPI) {
  // The watchdog is a separate extension: it counts down after agent_settled, then sends
  // a message to start a new run. This syncs the *watchdog's* state, not the agent's: only
  // it knows whether this settled run will rerun, so it is used only to suppress an early
  // finish (see the hasRunOnce gate).
  pi.events.on("watchdog:state", (raw: unknown) => {
    const state = raw as { running?: unknown; interrupted?: unknown } | undefined;
    watchdogActive = Boolean(state?.running);
    watchdogInterrupted = Boolean(state?.interrupted);
    applyEffectiveState();
  });

  // On start/hot reload, restore the written state from the pane source and recompute
  // once; then ask the watchdog's current state, so load order or a reload cannot miss an
  // earlier broadcast.
  pi.on("session_start" as any, async (event: any, ctx: any) => {
    // /new, /resume, /fork are "a different session": the old ✅ belongs to the old
    // context, so clear it. Reload (hot reload) and startup (new process, already cleared
    // on a clean exit) keep the pane's ✅.
    if (event?.reason !== "reload" && process.env.TMUX && process.env.TMUX_PANE) {
      sink.setPaneDone(false);
    }
    // No ✅ during the fix-up: clearing a leftover @pi_running=1 on reload is a
    // correction, not a finish.
    suppressDone = true;
    // The pane source can be a leftover (SIGKILL) or a real "agent still running during a
    // hot reload" — both look like @pi_running=1. Ask once more if the session is really
    // busy (ctx.isIdle); only if both agree is this run alive, so a leftover is not read
    // as running. If isIdle is unavailable, treat it as not busy.
    const factRunning = readPaneRunning();
    const busy = factRunning && ctx?.isIdle?.() === false;
    agentRunning = busy;
    hasRunOnce = busy;
    watchdogActive = false;
    watchdogInterrupted = false;
    pausedByPrompt = false;
    // Leave the pane source unchanged; the setTimeout fix-up below decides whether to clear it.
    isMarkedRunning = factRunning;
    // Read the pane option straight: a window with @pi_done=1 shows ✅ right away, and
    // other session_starts do not affect it.
    broadcastStatus();
    // Wait one tick so all extensions' session_start handlers finish first: if the
    // watchdog auto-starts from env it broadcasts true first, then the query confirms the
    // final state. With no watchdog the query gets no answer and apply clears the old pane
    // markers left by a reload/abnormal exit.
    setTimeout(() => {
      watchdogActive = false;
      watchdogInterrupted = false;
      pi.events.emit("watchdog:state:query");
      applyEffectiveState();
      suppressDone = false; // must run after apply, or the fix-up path could set a false ✅
    }, 0);
  });

  pi.on("agent_start" as any, async () => {
    hasRunOnce = true;
    agentRunning = true;
    applyEffectiveState();
  });

  // agent_settled only means Pi's current run fully settled; if the watchdog is still
  // active it will send a message and rerun after its countdown, so stay running and do
  // not call it done early.
  pi.on("agent_settled" as any, async () => {
    agentRunning = false;
    applyEffectiveState();
  });

  // During a blocking UI prompt (plan review select/editor, etc.) we are really waiting on
  // a person, so drop ⏳ for now even if the agent/watchdog has not ended; restore the real
  // state when the prompt ends.
  pi.on("ui_prompt_start" as any, async () => {
    if (pausedByPrompt) return;
    pausedByPrompt = true;
    // A new interaction (prompt) has started, so the old ✅ no longer describes the current
    // state; on a pure done->prompt path applyEffectiveState would early-return because
    // running did not change, so clear and broadcast here.
    if (process.env.TMUX && process.env.TMUX_PANE) sink.setPaneDone(false);
    applyEffectiveState();
    broadcastStatus();
  });
  pi.on("ui_prompt_end" as any, async (_event: any, ctx: any) => {
    if (!pausedByPrompt) return;
    pausedByPrompt = false;
    // If this run really ended during the popup (e.g. aborted), fix agentRunning to match.
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
  // kill-pane / close tab / close window sends SIGHUP; not clearing it leaves the window count too high
  process.on("SIGHUP", () => {
    cleanupOnExit();
    process.exit(0);
  });
}
