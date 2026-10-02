#!/usr/bin/env python3
"""看一眼即视为已读：清掉某个 tmux window 的 ✅（done-unseen）并重算状态投影。

tmux 的 ``after-select-window`` / ``after-select-pane`` hook 在你切到某个 window /
pane 时以该 window id 调用本脚本。它把该 window 内各 pane 的 ``@pi_done`` 清掉，
并立刻重算 ``@pi_win``（窗口栏）与 ``@pi_total``（kitty 标题 / 会话计数），
这样 ✅ 不必等 Pi 扩展的下一个事件才消失——空转的 agent 可能永远等不到下一个事件。

之所以要重算：``@pi_win`` / ``@pi_total`` 是扩展从 pane 级 ``@pi_running`` /
``@pi_done`` 算出的投影。清掉事实源后若不重算，状态栏上的 ✅ 会一直残留。

重算规则与 ``kitty-tab-sync.ts`` 的 ``broadcastStatus()`` 保持一致，改一处要同步另一处：
  @pi_win  逐 window：任一 @pi_running=1 → "⏳ "；否则任一 @pi_done=1 → "✅ "；否则 ""
  @pi_total 逐 session："⏳N ✅M "（为 0 的部分省略；全 0 时为 ""）
"""

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

SEP = "\x1f"
TIMEOUT = 2
WINDOW_RUNNING = "⏳ "
WINDOW_DONE = "✅ "


@dataclass(frozen=True)
class Pane:
    window_id: str
    pane_id: str
    running: bool
    done: bool


def tmux(args: Sequence[str]) -> str:
    """执行 tmux 子命令；超时 / 出错 / 非 tmux 环境一律回退为空串，绝不阻塞。"""
    try:
        return subprocess.check_output(
            ["tmux", *args], text=True, stderr=subprocess.DEVNULL, timeout=TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def session_of(window_id: str) -> str:
    return tmux(["display-message", "-p", "-t", window_id, "#{session_id}"]).strip()


def read_panes(session_id: str) -> list[Pane]:
    fmt = SEP.join(("#{window_id}", "#{pane_id}", "#{@pi_running}", "#{@pi_done}"))
    panes = []
    for line in tmux(["list-panes", "-s", "-t", session_id, "-F", fmt]).splitlines():
        fields = line.split(SEP)
        if len(fields) != 4 or not fields[0]:
            continue
        panes.append(Pane(fields[0], fields[1], fields[2] == "1", fields[3] == "1"))
    return panes


def window_status(panes: Sequence[Pane]) -> str:
    if any(pane.running for pane in panes):
        return WINDOW_RUNNING
    if any(pane.done for pane in panes):
        return WINDOW_DONE
    return ""


def session_total(panes: Sequence[Pane]) -> str:
    running = sum(pane.running for pane in panes)
    done = sum(pane.done for pane in panes)
    parts = []
    if running:
        parts.append(f"⏳{running}")
    if done:
        parts.append(f"✅{done}")
    if not parts:
        return ""
    return " ".join(parts) + " "


def ack(window_id: str) -> None:
    session_id = session_of(window_id)
    if not session_id:
        return
    panes = read_panes(session_id)
    # 目标 window 内已完成的 pane 视为已读：写回 tmux（持久），并在本地置否用于本次重算
    for pane in panes:
        if pane.window_id == window_id and pane.done:
            tmux(["set", "-pu", "-t", pane.pane_id, "@pi_done"])
    updated = [
        Pane(pane.window_id, pane.pane_id, pane.running, False)
        if pane.window_id == window_id
        else pane
        for pane in panes
    ]

    windows: dict[str, list[Pane]] = {}
    for pane in updated:
        windows.setdefault(pane.window_id, []).append(pane)
    for window, group in windows.items():
        tmux(["set", "-wq", "-t", window, "@pi_win", window_status(group)])
    tmux(["set", "-q", "-t", session_id, "@pi_total", session_total(updated)])
    tmux(["refresh-client", "-S"])


def main(argv: Sequence[str]) -> int:
    if len(argv) > 1 and argv[1]:
        ack(argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
