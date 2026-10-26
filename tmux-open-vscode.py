#!/usr/bin/env python3
"""Open the pane's folder in VS Code on the Mac, from the VPS.

Called by tmux's ``bind v`` with the pane path: run-shell "tmux-open-vscode.py <path>".

Two paths to the Mac, tried in order:
  1. An SSH RemoteForward tunnel: write the path, the Mac listener runs
     `open vscode://vscode-remote/...` — a one-keystroke popup window.
  2. Fallback: OSC 52 (tmux passthrough wrap) copies the full URL to the Mac
     clipboard; you then press a global hotkey that runs `open "$(pbpaste)"`.

Which tunnel we use is the whole story here. A fixed remote TCP port is ONE
global resource: the first connection to log in binds it, every later connection
fails to bind (EADDRINUSE), and tssh does not implement ExitOnForwardFailure —
it prints one warning and carries on. So every loser quietly believes it has a
tunnel, and when the winner's transport dies, `bind v` on any tab writes into
nothing. That is why each connection now forwards its own VPS socket path
(scripts/tssh-vps.sh) and this helper probes every path left behind, using the
first one that answers.

Every failure prints WHY to stderr — tmux's run-shell shows it briefly with `-d`,
and it lands in tmux's message log, so a silent no-op is never the answer.

`--probe` checks the whole chain without opening anything: it sends a URL the Mac
listener must reject, so a reply proves socket + tunnel + listener are alive while
`open` is never called.
"""

import base64
import glob
import os
import socket
import stat
import sys
import time

# VPS-side socket paths, one per tssh connection (must match scripts/tssh-vps.sh)
SOCK_PREFIX = "/tmp/vscode-open-remote"
# The Mac-side listener port; every forward shape ends up connecting to it
LISTENER_PORT = 19998
# Old shape: one fixed VPS port. Kept last so a ~/.ssh/config RemoteForward line,
# or an older wrapper, still works during the transition. Both stacks, because the
# forward binds 127.0.0.1 and [::1] separately and we have seen it keep only one.
LEGACY_ADDRS = [("127.0.0.1", LISTENER_PORT), ("::1", LISTENER_PORT)]
# The listener only accepts URLs under this prefix (must match its whitelist)
# VS Code only registers vscode:// with Launch Services, so vscode-remote:// is
# rejected with kLSApplicationNotFoundErr. The vscode://vscode-remote/... form opens fine.
URL_PREFIX = "vscode://vscode-remote/ssh-remote+vps/"
# Keep each step short: this runs on a keypress, not in the background
CONNECT_TIMEOUT = 0.5
# A healthy path acks in about one round trip (the listener answers before it opens
# anything), but this link is slow: measured 330ms median, 1.0s p90, 1.8s worst.
# A 1s deadline called that healthy chain dead, so keep real headroom.
REPLY_TIMEOUT = 2.5
# Whole-hunt budget. Generous on purpose: only a truly dead tunnel costs the full
# REPLY_TIMEOUT per candidate, `bind v` runs with `run-shell -b` so nothing freezes,
# and stopping early would hide the one socket that actually works.
BUDGET = 15.0


def _label(kind: str, target) -> str:
    """Human name for a tunnel, for the messages we print."""
    return target if kind == "unix" else f"{target[0]}:{target[1]}"


def _targets() -> list[tuple[str, object]]:
    """Every tunnel worth trying, per-connection sockets first, fixed port last.

    No pre-probe here on purpose: a dead file refuses the connect instantly, so
    filtering it out first would only double the connections we open — and every
    opened connection walks the whole VPS -> Mac tunnel, waking the listener for
    nothing. The attempt itself is the cheapest possible test.
    """
    found = []
    for path in glob.glob(SOCK_PREFIX + "*"):
        try:
            st = os.stat(path)
        except OSError:
            continue  # vanished between glob and stat
        if stat.S_ISSOCK(st.st_mode):
            found.append((st.st_mtime, path))
    found.sort(reverse=True)  # newest first, only to keep the order predictable
    targets: list[tuple[str, object]] = [("unix", path) for _, path in found]
    targets += [("tcp", addr) for addr in LEGACY_ADDRS]
    return targets


def _attempt(kind: str, target, url: str, timeout: float):
    """One shot at one tunnel. Returns (reply, None) or (None, why it failed)."""
    sock = None
    try:
        if kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(target)
        else:
            sock = socket.create_connection(target, timeout=CONNECT_TIMEOUT)
        sock.settimeout(timeout)
        sock.sendall(url.encode())
        return sock.recv(4096).decode(errors="replace").strip(), None
    except OSError as e:
        # TimeoutError and ConnectionRefusedError both land here; the name says which
        return None, f"{type(e).__name__}: {e}"
    finally:
        if sock is not None:
            sock.close()


def via_tunnel(path: str) -> str | None:
    """Send the path through some live tunnel; None means one of them handled it."""
    # URL_PREFIX already ends with '/', and path is absolute, so strip to avoid a '//'
    url = URL_PREFIX + path.lstrip("/")
    deadline = time.monotonic() + BUDGET
    tried = []
    for kind, target in _targets():
        left = deadline - time.monotonic()
        if left <= 0.05:
            tried.append("ran out of time")
            break
        reply, why = _attempt(kind, target, url, min(REPLY_TIMEOUT, left))
        if reply is None:
            # Dead peer or nothing listening: a roam can leave the listener alive
            # with its stream gone, so this is the common case, not an error
            tried.append(f"{_label(kind, target)}: {why}")
            continue
        if reply.startswith("OK"):
            return None
        if reply.startswith("rejected"):
            # The chain works and the Mac listener said no. Every other tunnel ends
            # at that same listener, so trying more would only repeat the answer.
            print(
                f"vscode listener rejected via {_label(kind, target)}: {reply}",
                file=sys.stderr,
            )
            return "failed"
        tried.append(f"{_label(kind, target)}: odd reply {reply!r}")
    print("no usable vscode tunnel — " + "; ".join(tried), file=sys.stderr)
    return "failed"


def probe() -> int:
    """Check the whole chain without opening a window.

    The URL is deliberately NOT under the whitelist, so the Mac listener answers
    "rejected" — that reply proves socket, tunnel and listener all work, and
    `open` is never called.
    """
    deadline = time.monotonic() + BUDGET
    problems = []
    for kind, target in _targets():
        left = deadline - time.monotonic()
        if left <= 0.05:
            problems.append("ran out of time")
            break
        reply, why = _attempt(kind, target, "probe-not-a-url", min(REPLY_TIMEOUT, left))
        if reply is not None and reply.startswith("rejected"):
            print(f"OK  {_label(kind, target)} -> Mac listener answered: {reply}")
            return 0
        problems.append(
            f"{_label(kind, target)}: {why or f'unexpected reply {reply!r}'}"
        )
    print(
        "!! no tunnel reaches the Mac listener — " + "; ".join(problems),
        file=sys.stderr,
    )
    return 2


def via_clipboard(path: str) -> None:
    """Fallback: OSC 52 copies the URL to the Mac clipboard via tmux passthrough."""
    url = URL_PREFIX + path.lstrip("/")
    payload = base64.b64encode(url.encode()).decode()
    # DCS tmux;... ST wrap lets OSC 52 pass through tmux to the outer terminal
    seq = f"\033Ptmux;\033\033]52;c;{payload}\a\033\\"
    sys.stdout.write(seq)
    sys.stdout.flush()
    print(
        f"tunnel down — URL copied to Mac clipboard, open it with the hotkey: {url}",
        file=sys.stderr,
    )


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] in ("-n", "--probe"):
        return probe()
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print("usage: tmux-open-vscode.py <absolute pane path> | --probe")
        return 0
    path = argv[1] if len(argv) > 1 else ""
    if not path:
        print(
            "usage: tmux-open-vscode.py <absolute pane path> | --probe", file=sys.stderr
        )
        return 1
    if not path.startswith("/"):
        print(f"not an absolute path: {path!r}", file=sys.stderr)
        return 1
    if via_tunnel(path) is not None:
        via_clipboard(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
