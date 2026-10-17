#!/usr/bin/env python3
"""Mac-side listener: receive a vscode-remote URL from the VPS and open it.

Runs on the Mac as a launchd service (see the plist next to this file). The VPS's
tmux `bind v` connects through the SSH RemoteForward tunnel (VPS port 19998 -> this
listener) and sends one URL per connection; we validate it and hand it to `open`.

Security: this socket is reachable by anything that can connect to the VPS's
127.0.0.1:19998, so we only accept URLs under the whitelist prefix and only ever
run `open` — no shell, no arbitrary command execution.
"""

import socket
import subprocess
import sys

# Keep in sync with tmux-open-vscode.py on the VPS. Use vscode://vscode-remote/...:
# macOS Launch Services only claims vscode://, plain vscode-remote:// is rejected.
URL_PREFIX = "vscode://vscode-remote/ssh-remote+vps/"
HOST = "127.0.0.1"
PORT = 19998


def handle(conn: socket.socket) -> None:
    with conn:
        try:
            url = conn.recv(4096).decode(errors="replace").strip()
        except OSError:
            return
        if not url:
            conn.sendall(b"empty request")
            return
        if not url.startswith(URL_PREFIX):
            conn.sendall(f"rejected: not under {URL_PREFIX}".encode())
            return
        # open handles vscode-remote:// and focuses/creates the right window
        subprocess.run(["open", url], check=False)
        conn.sendall(b"OK")


def main() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(4)
        print(f"vscode-open-listener on {HOST}:{PORT}", flush=True)
        while True:
            try:
                handle(srv.accept()[0])
            except OSError as e:
                print(f"accept/handle error: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
