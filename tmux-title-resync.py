#!/usr/bin/env python3
"""Re-send terminal titles when a tssh reconnect lands on this VPS.

The problem: tmux only sends a title OSC when the rendered title string
differs from its per-client cache. A tssh network drop kills the transport
but NOT the tmux client (the pty master lives in tsshd, which roams), so a
status change during the outage (⏳ → ✅) was rendered into the dead
connection and lost — while the cache already holds the new value. After
the reconnect tmux considers the title "unchanged" and never sends it
again: the Mac kitty tab stays stuck on a stale ⏳.

The signal we CAN see from this machine: tssh-vps.sh forwards kitty's
socket to a unique /tmp/mykitty-remote-* path per connection, and a roam
re-connecting into the same tsshd re-binds that file (new inode — proven
by the 260ee47 fix). So: watch the inodes of those socket files; when the
set changes, a (re)connect happened — force a title resend by toggling
each session's @pi_total off and on again. The two writes must be separate
tmux invocations: commands queued in one batch all run before the redraw,
so the intermediate state would never render and the cache would never
miss. Separate invocations = separate redraws = a guaranteed cache miss.

Launch: from tmux.conf via run-shell. tmux passes its TMUX env to run-shell
children, so every `tmux` call below talks to the SAME server that spawned
us (never to whatever the default socket points at). Steady state costs
one stat() per socket per second and zero network. A single-instance
pidfile keeps config reloads from stacking watchers — no pkill, which
could match unrelated processes.

tmux.conf line:
    run-shell -b "setsid $HOME/.local/bin/tmux-title-resync.py \\
        >/dev/null 2>&1 </dev/null &"
setsid matters: a plain background child of run-shell gets reaped when
tmux finishes the job. The loop exits by itself once its tmux server is
gone (has-session fails), so nothing lingers after the server dies.
"""

import os
import subprocess
import sys
import time
from collections.abc import Sequence

# Socket files live in /tmp and the roam re-bind is instant; one second is
# plenty and keeps the watcher cheap.
TICK = 1.0
# tssh-vps.sh appends a unique suffix per connection under this prefix.
FWD_PREFIX = "/tmp/mykitty-remote"
PIDFILE = "/tmp/tmux-title-resync.pid"


def run(args: Sequence[str]) -> str:
    """Run tmux; return stdout, "" on any failure (dead server, no tmux)."""
    try:
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def server_alive() -> bool:
    """True while the tmux server we are bound to still runs.

    The answer is the return code, not the output: `has-session` prints
    nothing when it succeeds, so an output check would read a healthy
    server as dead.
    """
    return (
        subprocess.run(
            ["tmux", "has-session"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).returncode
        == 0
    )


def socket_inodes() -> dict[str, tuple[int, int]]:
    """Socket path -> (st_dev, st_ino) for the tunnel sockets we can stat.

    A missing file yields nothing for that path, so a connection dying and
    a new one binding both register as a change of the returned dict.
    """
    out: dict[str, tuple[int, int]] = {}
    try:
        names = os.listdir("/tmp")
    except OSError:
        return out
    prefix = FWD_PREFIX.rsplit("/", 1)[-1]
    for name in names:
        if not name.startswith(prefix):
            continue
        path = f"/tmp/{name}"
        try:
            st = os.stat(path)
        except OSError:
            continue
        out[path] = (st.st_dev, st.st_ino)
    return out


def already_running() -> bool:
    """True when another copy of this script holds a live pid."""
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="replace")
    except OSError:
        # Dead pid or a recycled one running something else: not us
        return False
    return os.path.basename(__file__) in cmdline


def claim() -> bool:
    """Take the pidfile; False means another watcher is already serving."""
    if already_running():
        return False
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def nudge_titles() -> None:
    """Force every session's title to be re-sent.

    Toggling @pi_total off then on (two SEPARATE tmux calls, see module
    docstring) changes the rendered title twice; the second render differs
    from tmux's cache, so the OSC goes out. The title template reads the
    session option, so it must be each session's own, not -g.
    """
    sessions = run(
        ["tmux", "list-sessions", "-F", "#{session_id}\t#{@pi_total}"]
    ).splitlines()
    for line in sessions:
        session_id, _, total = line.partition("\t")
        if not session_id:
            continue
        run(["tmux", "set", "-uq", "-t", session_id, "@pi_total"])
        run(["tmux", "set", "-q", "-t", session_id, "@pi_total", total])


def main() -> int:
    if not claim():
        return 0
    # One full tick of silence before arming: a just-started tmux server
    # renders fresh clients anyway (empty title cache), so sockets that
    # already existed at startup must not trigger a redundant nudge.
    known = socket_inodes()
    while True:
        time.sleep(TICK)
        try:
            # Exit when our server dies instead of looping forever.
            if not server_alive():
                return 0
            current = socket_inodes()
            if current != known:
                known = current
                nudge_titles()
        except OSError:
            # Transient FS/proc errors must not kill the watcher
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: drop the pidfile so the next start is not blocked
        # by our own (now stale) entry
        try:
            with open(PIDFILE) as f:
                if f.read().strip() == str(os.getpid()):
                    os.unlink(PIDFILE)
        except OSError:
            pass
