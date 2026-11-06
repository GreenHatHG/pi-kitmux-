#!/usr/bin/env python3
"""Mac-side listener: receive a vscode-remote URL from the VPS and open it.

Runs on the Mac as a launchd service (see the plist next to this file). The VPS's
tmux `bind v` connects through the SSH RemoteForward tunnel (VPS socket -> here) and
sends one URL per connection; we validate it and hand it to the VS Code CLI.

One thread per request, on purpose. Launching VS Code can take seconds (cold start), and a
single-threaded loop would leave the next caller without an ack for that whole time
— and would let one silent client block everybody.

Security: this socket is reachable by anything that can connect to the VPS's
forwarded path, so we only accept URLs under the whitelist prefix and only ever
launch VS Code with that folder — no shell, no arbitrary command execution.
"""

import errno
import socket
import subprocess
import sys
import threading
import time


# Keep in sync with tmux-open-vscode.py on the VPS. Use vscode://vscode-remote/...:
# macOS Launch Services only claims vscode://, plain vscode-remote:// is rejected.
URL_PREFIX = "vscode://vscode-remote/ssh-remote+vps/"
HOST = "127.0.0.1"
PORT = 19998
# A client that connects and then says nothing must not hold a thread forever
RECV_TIMEOUT = 10.0
# accept() errors that mean "busy right now", not "stop serving"
TRANSIENT_ACCEPT_ERRORS = {errno.EMFILE, errno.ENFILE, errno.ENOBUFS, errno.ENOMEM}


# Same-folder → focuses its existing window, different folder → new window:
# this is VS Code's own native window logic for --folder-uri, no AppleScript needed.
# Absolute path: launchd's default PATH (/usr/bin:/bin:...) does not contain the
# `code` shim, which lives in /usr/local/bin or the app bundle.
CODE_BIN = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"


def handle(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(RECV_TIMEOUT)
        try:
            url = conn.recv(4096).decode(errors="replace").strip()
        except (OSError, ValueError):
            return
        if not url:
            conn.sendall(b"empty request")
            return
        if not url.startswith(URL_PREFIX) or "\0" in url:
            conn.sendall(f"rejected: not under {URL_PREFIX}".encode())
            return
        # Ack BEFORE launching: the caller only needs to know the URL arrived, and it will
        # retry on silence. Answering first makes every ack cost one round trip.
        try:
            conn.sendall(b"OK")
        except OSError:
            return  # caller gave up early; still open the URL, it asked for it
        # CLI returns immediately (no --wait); window choice is native: an already-open
        # folder URI is focused, anything else opens a new window
        remote_uri = "vscode-remote://ssh-remote+vps" + _folder_of(url)
        try:
            subprocess.run(
                [CODE_BIN, "--folder-uri", remote_uri],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as e:
            print(f"code failed for {remote_uri!r}: {e}", file=sys.stderr, flush=True)


def _folder_of(url: str) -> str:
    """The folder part of a whitelisted URL, e.g. .../ssh-remote+vps/a/b -> /a/b."""
    path = url[len(URL_PREFIX) :].split("?", 1)[0]
    return "/" + path.lstrip("/")


def serve(srv: socket.socket) -> None:
    failures = 0
    while True:
        try:
            conn = srv.accept()[0]
            failures = 0
        except OSError as e:
            # A closed socket would otherwise spin here forever, but running out of fds
            # is just a burst — back off and keep serving
            failures += 1
            if failures == 1:
                print(f"accept error: {e}", file=sys.stderr, flush=True)
            transient = e.errno in TRANSIENT_ACCEPT_ERRORS
            if not transient and failures >= 5:
                print("giving up on accept, exiting", file=sys.stderr, flush=True)
                return
            time.sleep(min(0.2 * failures, 2.0))
            continue
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


def main() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(16)
        print(f"vscode-open-listener on {HOST}:{PORT}", flush=True)
        serve(srv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
