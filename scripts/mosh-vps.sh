#!/usr/bin/env bash
# Mac-side wrapper: run mosh with an on-demand kitty remote-control tunnel.
#
# Why this exists: the VPS's `bind p` picker groups agents by kitty tab, which needs
# kitty's remote-control socket on the VPS. mosh cannot forward anything, so a plain
# ssh reverse tunnel carries the socket — and it should only live while you are
# actually using the box. This wrapper starts the tunnel before mosh and tears it
# down when you leave, so no idle connection is kept around.
#
# Usage (on the Mac):
#   mosh-vps                 # instead of: mosh vps
#   MOSH_VPS_HOST=mybox mosh-vps ...
# Runs mosh with --predict=never (no speculative local echo; typing always
# waits for the server echo).
#
# Prerequisites (Mac):
#   - autossh: brew install autossh
#   - kitty.conf: allow_remote_control + listen_on unix:/tmp/mykitty.sock
#     (a name NOT ending in digits -> kitty keeps the name fixed across restarts)
#   - ssh config Host "vps" (or $MOSH_VPS_HOST) that mosh can use
# VPS side needs nothing: the picker globs /tmp/mykitty-* and picks up the tunnel
# socket by itself; without a tunnel it just falls back to "No Kitty Tab".

set -uo pipefail
# No -e: mosh exits non-zero all the time (roam failures), that is normal, not an error.

HOST="${MOSH_VPS_HOST:-vps}"
# The VPS-side socket path. It is also the one string that appears in BOTH the
# autossh parent's and the ssh child's command line, so pgrep/pkill can target
# the whole pair (matching "autossh" alone misses the ssh child).
FWD='/tmp/mykitty-remote'
PAT="autossh.*${FWD}"

resolve_socket() {
  # $KITTY_LISTEN_ON is the ground truth inside kitty's own terminals
  local p="${KITTY_LISTEN_ON#unix:}"
  if [ -n "$p" ] && [ -S "$p" ]; then
    printf '%s' "$p"
    return 0
  fi
  # launchd shells and fresh terminals do not have it: probe the newest sockets.
  # The probe matters — /tmp/mykitty-<PID> keeps the PID of a past kitty instance,
  # so a blind newest-first pick would happily forward a dead socket.
  local c
  for c in $(ls -t /tmp/mykitty-* 2>/dev/null); do
    if kitty @ --to="unix:$c" ls >/dev/null 2>&1; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

cleanup() { pkill -f "$PAT" 2>/dev/null || true; }
trap cleanup EXIT

sock="$(resolve_socket)"
if [ -z "$sock" ]; then
  # Not a hard failure: everything except tab grouping works without the tunnel
  echo "mosh-vps: no live kitty socket found; connecting without tab grouping" >&2
  exec mosh --predict=never "$HOST" "$@"
fi

# One tunnel only: a leftover pair (terminal window closed without exit) is
# picked up here instead of being duplicated
if ! pgrep -f "$PAT" >/dev/null; then
  # -M 0: let ssh's ServerAlive* decide liveness (autossh's own echo port is pointless
  # for a -N tunnel). ExitOnForwardFailure makes a refused forward fail fast instead
  # of sitting on a half-open connection that forwards nothing.
  autossh -M 0 -N -f \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -R "${FWD}:${sock}" "$HOST"
  # -f returns before the ssh child is fully up; give it a moment, then report
  # honestly instead of letting the user wonder why grouping is missing
  ok=""
  for _ in 1 2 3 4 5; do
    if pgrep -f "$PAT" >/dev/null; then ok=1; break; fi
    sleep 1
  done
  if [ -z "$ok" ]; then
    echo "mosh-vps: tunnel failed to start; connecting without tab grouping" >&2
  fi
fi

mosh --predict=never "$HOST" "$@"
rc=$?

# pkill once is not always enough (the ssh child may outlive the autossh parent),
# so sweep until the pair is gone
for _ in 1 2 3 4 5; do
  pgrep -f "$PAT" >/dev/null || break
  pkill -f "$PAT" 2>/dev/null
  sleep 1
done

exit "$rc"
