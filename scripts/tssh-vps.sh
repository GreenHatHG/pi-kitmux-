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

sock="$(resolve_socket)"
if [ -z "$sock" ]; then
  # Not a hard failure: everything except tab grouping works without the tunnel
  echo "tssh-vps: no live kitty socket found; connecting without tab grouping" >&2
  exec tssh "$HOST" "$@"
fi

# No StreamLocalBindUnlink any more: it exists to steal a path from someone else,
# and the whole point of the unique suffix is that there is nobody to steal from.
# NOTE: tssh prints forwarding failures as a plain warning and connects anyway
# (ExitOnForwardFailure is not honoured today), so a broken tunnel is silent — check it
# with scripts/verify-kitty-tunnel.sh after changing anything here. The option stays so a
# future tssh that does honour it fails loudly instead of silently dropping the tunnel.
# 19998 is the VS Code opener port: tmux-open-vscode.py on the VPS connects to
# 127.0.0.1:19998, and this forward carries it to the Mac-side listener
# (scripts/vscode-open-listener.py, launchd). TCP port is fixed, no PID problem.
# Binding the remote port succeeds even when the Mac listener is down — the
# helper then just gets connection refused and falls back to OSC 52.
# NOTE: if ~/.ssh/config already has a RemoteForward 19998 line for Host vps,
# remove it — the duplicate bind only warns, but two listeners on one port
# means whichever connects first wins, so the helper's tunnel may be the loser.
exec tssh \
  -o ExitOnForwardFailure=yes \
  -R "${FWD}:${sock}" \
  -R 19998:127.0.0.1:19998 \
  "$HOST" "$@"
