#!/usr/bin/env python3
"""Mark a tmux window as seen: clear its ✅ and rebuild the status at once.

tmux runs this on ``after-select-window`` / ``after-select-pane`` when you switch
to a window or pane, and hands it that window id. It clears ``@pi_done`` on the
window's panes, then recomputes ``@pi_win`` (window bar) and ``@pi_total`` (kitty
title / session count). Without this, a ✅ waits for the next Pi event — and an
idle agent may never send one.

Why recompute: ``@pi_win`` / ``@pi_total`` are views the extension builds from
pane-level ``@pi_running`` / ``@pi_done``. Once you clear the source you must
recompute, or the ✅ stays on screen.

Keep the rules the same as ``broadcastStatus()`` in ``kitty-tab-sync.ts``: if you
change one, change the other:
  @pi_win   per window: any @pi_running=1 -> "⏳ "; else any @pi_done=1 -> "✅ "; else ""
  @pi_total per session: "⏳N ✅M " (skip a zero part; "" when both are zero)
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
    """Run a tmux subcommand; on timeout, error, or no tmux, return "" and never block."""
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
    # A done pane in this window counts as seen: clear it in tmux and in our local copy.
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
