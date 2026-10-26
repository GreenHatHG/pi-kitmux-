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
# Usage (on the Mac):
#   tssh-vps                 # instead of: tssh vps
#   TSSH_VPS_HOST=mybox tssh-vps ...
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

sock="$(resolve_socket)"
if [ -z "$sock" ]; then
  # Not a hard failure: tab grouping is what needs the kitty socket, `bind v` does not
  echo "tssh-vps: no live kitty socket found; connecting without tab grouping" >&2
  exec tssh "${VSCODE_ARGS[@]}" "$HOST" "$@"
fi

# No StreamLocalBindUnlink any more: it exists to steal a path from someone else,
# and the whole point of the unique suffix is that there is nobody to steal from.
# NOTE: tssh prints forwarding failures as a plain warning and connects anyway
# (ExitOnForwardFailure is not implemented in tssh, checked in the 0.1.27 source), so a
# broken tunnel is silent — check it with scripts/verify-kitty-tunnel.sh after changing
# anything here. The option stays so a future tssh that does honour it fails loudly
# instead of silently dropping the tunnel.
exec tssh \
  "${VSCODE_ARGS[@]}" \
  -R "${FWD}:${sock}" \
  "$HOST" "$@"
