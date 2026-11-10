#!/usr/bin/env bash
# Mac-side wrapper: connect to the VPS with tssh, carrying the kitty
# remote-control socket as a reverse tunnel.
#
# Why a wrapper at all: kitty's listen_on ALWAYS embeds its PID
# (/tmp/mykitty-<PID> — {kitty_pid} placeholder or auto-appended, there is no
# fixed-name option), so a static RemoteForward line in ssh config would point
# at a dead socket after the first kitty restart. This script resolves the
# LIVE socket right before connecting and hands it to `tssh -R`; when tssh
# exits, the tunnel dies with the connection — nothing to clean up.
#
# Auto-recovery uses tssh's OWN built-in reconnect (--reconnect, v0.1.25+): after a
# drop it restarts the client and re-logs in, and a fresh login re-binds every -R
# forward (forwards only bind at login, never on roam). -f runs it in the background
# so the restart is hands-off; UdpSessionAttach re-attaches to the SAME remote
# session instead of spawning a new shell. So we do NOT wrap this in a while-loop —
# tssh's --reconnect re-logins and rebuilds the sockets by itself.
#
# One command, two jobs: the tunnel daemon runs in the BACKGROUND (-f -N: fork, no
# remote shell), then this script drops you into a FOREGROUND interactive shell via
# a separate `tssh HOST`. tssh has no single-process "detach forwards but keep a
# shell" mode (verified in tssh source: -f backgrounds the whole invocation), so the
# wrapper starts the daemon, then execs the shell. The daemon and the shell are two
# independent connections; closing the shell does NOT kill the tunnel.
#
# SINGLETON, not one-per-run: -f --reconnect spawns a supervisor (TRZSZ-SSH-BG-MONITOR)
# that respawns the client forever, and an orphaned client used to linger on the VPS
# for UdpAliveTimeout's default ~10 days. One daemon per tssh-vps run therefore stacked
# up (7 orphans seen live on 2026-11-09). The wrapper starts the daemon ONLY if none is
# running (marker below); re-runs reuse the existing tunnel instead of stacking another.
#
# NO UdpSessionAttach/UdpSessionName-for-attach: source-verified (tssh 0.1.27, ssh.go
# forceExit) that in attach mode SIGTERM to the client is a DETACH — the remote tsshd
# keeps every forward for the full alive timeout instead of closing. A -N client has
# no shell session to re-attach to, so attach bought nothing. Plain --reconnect
# re-logins and re-binds all -R forwards on its own. UdpAliveTimeout=600 caps any
# client-less remote session at 10 minutes (client-side value, sent to tsshd at
# connect; there is no server-side knob).
#
# Usage (on the Mac):
#   tssh-vps                 # instead of: tssh vps
#   TSSH_VPS_HOST=mybox tssh-vps ...
#   tssh-vps --stop-tunnel   # kill the shared tunnel daemon (monitor + client)

# --stop-tunnel: -f --reconnect runs a supervisor (TRZSZ-SSH-BG-MONITOR) that
# respawns the client forever, so killing the monitor alone leaves the current
# client child alive. Kill the whole process group instead.
if [ "${1:-}" = "--stop-tunnel" ]; then
  marker="kitmux-tunnel-daemon"
  pgid=$(pgrep -f "UdpSessionName=${marker}" 2>/dev/null | head -1)
  if [ -n "$pgid" ]; then
    pgid=$(ps -o pgid= -p "$pgid" | tr -d ' ')
    kill -- -"$pgid" && echo "tssh-vps: tunnel daemon (pgid ${pgid}) stopped"
  else
    echo "tssh-vps: no tunnel daemon running"
  fi
  exit 0
fi
#
# Each run forwards to its OWN unique VPS path (/tmp/mykitty-remote-<pid>-<rand>),
# so concurrent connections never steal the path from each other.
#
# Prerequisites (Mac):
#   - tssh installed (brew install trzsz-ssh / cargo install trzsz-ssh)
#   - kitty.conf: allow_remote_control + listen_on unix:/tmp/mykitty
# VPS side needs nothing: the picker globs /tmp/mykitty-* and picks up the
# tunnel socket by itself; without a tunnel it just falls back to "No Kitty Tab".

set -uo pipefail

HOST="${TSSH_VPS_HOST:-vps}"
# The VPS-side path PREFIX the picker globs for. Each connection appends its own
# unique suffix, so two connections can never fight over one path.
FWD_PREFIX='/tmp/mykitty-remote'
# Same shape for the VS Code opener: one socket per connection, globbed by
# tmux-open-vscode.py on the VPS side (must match its SOCK_PREFIX).
VSCODE_FWD_PREFIX='/tmp/vscode-open-remote'
# Where the Mac-side listener sits; the remote socket forwards to this local port
VSCODE_PORT=19998

resolve_socket() {
  # $KITTY_LISTEN_ON is the ground truth inside kitty's own terminals.
  # The :- guard matters: the script runs with `set -u`, and a plain bash shell
  # (or launchd) does not export this variable at all.
  local p="${KITTY_LISTEN_ON:-}"
  p="${p#unix:}"
  if [ -n "$p" ] && [ -S "$p" ]; then
    printf '%s' "$p"
    return 0
  fi
  # launchd shells and fresh terminals do not have it: probe the newest sockets.
  # The probe matters — /tmp/mykitty-<PID> keeps the PID of a past kitty
  # instance, so a blind newest-first pick would forward a dead socket.
  local c
  for c in $(ls -t /tmp/mykitty-* 2>/dev/null); do
    if kitty @ --to="unix:$c" ls >/dev/null 2>&1; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

# Why the suffix: tsshd keeps a connection alive across network drops, and the
# forwarded socket file only disappears when the owning connection ends. With one
# shared fixed name, a NEW connection had to unlink the old path to bind — which
# orphaned every older connection (their listener lived on, unreachable), and when
# the newest one closed it took the path away from everyone. That is the
# "no kitty tab" state. A per-connection name removes the conflict entirely:
# a reconnect to the SAME tsshd finds its own path still there. $RANDOM covers the
# case of the same shell calling us twice while the old connection is still alive.
FWD="${FWD_PREFIX}-$$-${RANDOM}"

# The VS Code opener, same trick one level down. The old line forwarded a FIXED
# remote TCP port (19998); a port is a single global resource, so the first
# connection to log in won it and every later one died with EADDRINUSE. tssh does
# not implement ExitOnForwardFailure (verified in the 0.1.27 source) — it prints
# one warning and carries on — so every loser believed it had a tunnel, and when
# the winner's transport dropped, `bind v` on any tab wrote into nothing. A
# per-connection VPS socket path removes the fight: remote unix socket -> the
# Mac's 127.0.0.1:19998 listener (scripts/vscode-open-listener.py, launchd).
# tssh accepts this shape (/remote_socket:host:port) and needs an absolute path;
# binding succeeds even while the Mac listener is down, which the helper then
# sees as a dead tunnel and answers with the OSC 52 fallback.
# NOTE: a stale socket file at this exact path would make the bind fail (only a
# server-side StreamLocalBindUnlink yes overrides that); $$+$RANDOM makes a
# collision vanishingly unlikely, and the helper skips orphaned files anyway.
# NOTE: remove any RemoteForward 19998 line from ~/.ssh/config for Host vps — it
# is the old shape and would put the port back under contention.
VSCODE_FWD="${VSCODE_FWD_PREFIX}-$$-${RANDOM}"
# Declared here, not below: even the no-kitty-socket branch keeps this tunnel
VSCODE_ARGS=(-o ExitOnForwardFailure=yes -R "${VSCODE_FWD}:127.0.0.1:${VSCODE_PORT}")

# The doubao translation server (launchd on the Mac, port 8765), same one-socket-
# per-connection shape. The pi doubao-translate extension on the VPS globs
# /tmp/doubao-http-* for the newest live socket; no listener on the Mac side just
# means translations fail silently there, never the login itself.
DOUBAO_FWD="/tmp/doubao-http-$$-${RANDOM}"

# Inert marker for the singleton guard and --stop-tunnel. Never set
# UdpSessionAttach=yes with it: a second attach would detach the first client.
UDP_SESSION="kitmux-tunnel-daemon"

# Singleton guard: exactly one tunnel daemon, shared by all tssh-vps runs.
if pgrep -f "UdpSessionName=${UDP_SESSION}" >/dev/null 2>&1; then
  echo "tssh-vps: tunnel daemon already running; reusing it" >&2
  exec tssh "$HOST" "$@"
fi

sock="$(resolve_socket)"
if [ -z "$sock" ]; then
  # Not a hard failure: tab grouping is what needs the kitty socket, `bind v` does not
  echo "tssh-vps: no live kitty socket found; connecting without tab grouping" >&2
  tssh --reconnect -f -N -o UdpAliveTimeout=600 -o UdpSessionName="${UDP_SESSION}" \
    "${VSCODE_ARGS[@]}" -R "${DOUBAO_FWD}:127.0.0.1:8765" "$HOST" "$@"
  exec tssh "$HOST" "$@"
fi

# No StreamLocalBindUnlink any more: it exists to steal a path from someone else,
# and the whole point of the unique suffix is that there is nobody to steal from.
# NOTE: tssh prints forwarding failures as a plain warning and connects anyway
# (ExitOnForwardFailure is not implemented in tssh, checked in the 0.1.27 source), so a
# broken tunnel is silent — check it with scripts/verify-kitty-tunnel.sh after changing
# anything here. The option stays so a future tssh that does honour it fails loudly
# instead of silently dropping the tunnel.
# The background daemon carries the kitty socket, VS Code, and doubao forwards.
# -N (tunnel-only) + -f (fork to background) + --reconnect. It returns immediately;
# the `exec tssh HOST` below then gives the interactive shell.
tssh --reconnect -f -N -o UdpAliveTimeout=600 -o UdpSessionName="${UDP_SESSION}" \
  "${VSCODE_ARGS[@]}" \
  -R "${FWD}:${sock}" \
  -R "${DOUBAO_FWD}:127.0.0.1:8765" \
  "$HOST" "$@"

# Foreground interactive shell (separate connection). Exiting it does not kill the daemon.
exec tssh "$HOST" "$@"
