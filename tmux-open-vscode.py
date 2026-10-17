#!/usr/bin/env python3
"""Open the pane's folder in VS Code on the Mac, from the VPS.

Called by tmux's ``bind v`` with the pane path: run-shell "tmux-open-vscode.py <path>".

Two paths to the Mac, tried in order:
  1. The SSH RemoteForward tunnel (VPS port 19998 -> Mac listener): write the path,
     the Mac listener runs `open vscode-remote://...` — a one-keystroke popup window.
  2. Fallback: OSC 52 (tmux passthrough wrap) copies the full URL to the Mac clipboard;
     you then press a global hotkey that runs `open "$(pbpaste)"`.

Every failure prints WHY to stderr — tmux's run-shell shows it briefly with `-d`,
and it lands in tmux's message log, so a silent no-op is never the answer:
  - listener unreachable: tunnel is down (no VS Code / no RemoteForward) -> fallback
  - listener said no: rejected (bad prefix etc.), message passed through
  - listener hung: 3s timeout
"""

import base64
import socket
import subprocess
import sys

# Port the Mac-side listener watches; the ssh config RemoteForward maps it back
TUNNEL_PORT = 19998
# The listener only accepts URLs under this prefix (must match its whitelist)
# VS Code only registers vscode:// with Launch Services, so vscode-remote:// is
# rejected with kLSApplicationNotFoundErr. The vscode://vscode-remote/... form opens fine.
URL_PREFIX = "vscode://vscode-remote/ssh-remote+vps/"
TIMEOUT = 3


def via_tunnel(path: str) -> str | None:
    """Send the path through the tunnel; None means the tunnel is not there."""
    # URL_PREFIX already ends with '/', and path is absolute, so strip to avoid a '//'
    url = URL_PREFIX + path.lstrip("/")
    try:
        with socket.create_connection(("127.0.0.1", TUNNEL_PORT), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            s.sendall(url.encode())
            reply = s.recv(4096).decode(errors="replace").strip()
        if reply.startswith("OK"):
            return None
        # Listener reached but refused: say exactly what it said
        print(f"vscode listener rejected: {reply or 'empty reply'}", file=sys.stderr)
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        print(f"tunnel down (RemoteForward not connected?): {e}", file=sys.stderr)
    return "failed"


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
    path = argv[1] if len(argv) > 1 else ""
    if not path:
        print("usage: tmux-open-vscode.py <pane path>", file=sys.stderr)
        return 1
    if not path.startswith("/"):
        print(f"not an absolute path: {path!r}", file=sys.stderr)
        return 1
    if via_tunnel(path) is not None:
        via_clipboard(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
