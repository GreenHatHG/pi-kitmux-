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
# Prerequisites (Mac):
#   - tssh installed (brew install trzsz-ssh / cargo install trzsz-ssh)
#   - kitty.conf: allow_remote_control + listen_on unix:/tmp/mykitty
# VPS side needs nothing: the picker globs /tmp/mykitty-* and picks up the
# tunnel socket by itself; without a tunnel it just falls back to "No Kitty Tab".

set -uo pipefail

HOST="${TSSH_VPS_HOST:-vps}"
# The VPS-side socket path the picker globs for.
FWD='/tmp/mykitty-remote'

resolve_socket() {
  # $KITTY_LISTEN_ON is the ground truth inside kitty's own terminals
  local p="${KITTY_LISTEN_ON#unix:}"
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

sock="$(resolve_socket)"
if [ -z "$sock" ]; then
  # Not a hard failure: everything except tab grouping works without the tunnel
  echo "tssh-vps: no live kitty socket found; connecting without tab grouping" >&2
  exec tssh "$HOST" "$@"
fi

# StreamLocalBindUnlink: drop a stale /tmp/mykitty-remote left by a previous
# ssh session before binding; ExitOnForwardFailure: fail loudly instead of
# sitting on a connection that forwards nothing.
# 19998 is the VS Code opener port: tmux-open-vscode.py on the VPS connects to
# 127.0.0.1:19998, and this forward carries it to the Mac-side listener
# (scripts/vscode-open-listener.py, launchd). TCP port is fixed, no PID problem.
# Binding the remote port succeeds even when the Mac listener is down — the
# helper then just gets connection refused and falls back to OSC 52.
# NOTE: if ~/.ssh/config already has a RemoteForward 19998 line for Host vps,
# remove it — a duplicate bind + ExitOnForwardFailure would kill the connection.
exec tssh \
  -o ExitOnForwardFailure=yes \
  -o StreamLocalBindUnlink=yes \
  -R "${FWD}:${sock}" \
  -R 19998:127.0.0.1:19998 \
  "$HOST" "$@"
