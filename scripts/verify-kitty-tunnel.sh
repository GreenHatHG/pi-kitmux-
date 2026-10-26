#!/usr/bin/env bash
# Check whether the tssh -> VPS -> Mac kitty tunnel is actually usable.
#
# Two modes:
#   scripts/verify-kitty-tunnel.sh --now     # health-check the connections alive right now
#   scripts/verify-kitty-tunnel.sh           # wait for the NEXT connection, then check it
#
# The second mode answers the real question: after a network drop and reconnect, does
# tssh build the forwarded path again, and can kitty be driven through it?
#
# Exit codes: 0 = works, 1 = nothing to check / no new connection, 2 = path or socket broken.
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:---wait}"
WAIT="${WAIT:-300}"

case "$MODE" in
  --now|--wait) ;;
  -h|--help)
    sed -n '3,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "unknown argument: $MODE (use --now, --wait, or --help)" >&2
    exit 64
    ;;
esac

# tsshd keeps its listening control socket on fd 1, so fd 1 tells us what it forwarded.
tsshd_pids() { ps -eo pid=,comm= | awk '$2 == "tsshd" {print $1}'; }
path_of_inode() { awk -v want="$1" '$7 == want {print $8; exit}' /proc/net/unix; }

# Every path some live connection claims, one per line, deduped.
claimed_paths() {
  local pid inode
  for pid in $(tsshd_pids); do
    inode=$(readlink "/proc/$pid/fd/1" 2>/dev/null | tr -dc '0-9')
    [ -n "$inode" ] && path_of_inode "$inode"
  done | sort -u
}

# Check one path the way the picker uses it: file on disk + kitty answers + tags decode.
check_path() {
  local path="$1" listeners tag_ok
  echo "  path=[$path]"

  # The kernel keeps a listener alive after its file is gone; that listener can never
  # be reached again, and that is exactly the state the picker cannot recover from.
  # Exit 3 (not 2): this connection's tunnel is dead, but a path the picker cannot see
  # does not hurt it, and every connection made before the per-path fix looks like
  # this — so it must not fail the whole report forever.
  if [ ! -e "$path" ]; then
    echo "     !! no file on disk: this connection's tunnel is dead (orphan listener)"
    return 3
  fi

  # How many live listeners claim this same path. More than one means every binder but
  # the newest is shadowed — which one answers cannot be told from either side.
  # ss prints "<path> <inode> * 0", so the path column is not the last field.
  listeners=$(ss -xl 2>/dev/null | grep -c "[[:space:]]${path}[[:space:]]")
  if [ "${listeners:-0}" -gt 1 ]; then
    echo "     !! ${listeners} live listeners on this one path: all but the newest are shadowed"
  fi

  if ! out=$(timeout 8 kitty @ --to="unix:$path" ls 2>&1); then
    echo "     !! file exists but kitty does not answer"
    echo "$out" | head -n 2 | sed 's/^/     /'
    return 2
  fi

  # Decode the tab tag exactly like the picker does, so we see the real grouping.
  # The helper exits 3 when kitty answers but no session maps to a tab: the picker
  # would still show "No Kitty Tab" for everything, so that is a failure, not a pass.
  KV_SOCK="unix:$path" python3 - "$root/switch-pi-agent.py" <<'PY'
import importlib.util, os, re, sys
spec = importlib.util.spec_from_file_location("spa", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sock = os.environ["KV_SOCK"]
m._socket_candidates = lambda: [sock]      # pin to the socket under test
m._working_socket = None
panes, tabs = m.get_kitty_tabs()
sess = m.map_sessions_by_title(tabs)
strip = lambda s: re.sub(r"[\u200b\u200c\u2060\ufeff]", "", s)
print(f"     OK — kitty answers, {len(panes)} panes / {len(tabs)} tabs")
if not sess:
    print("     !! no host:session tag matched, grouping would show No Kitty Tab")
for k, v in sorted(sess.items()):
    print(f"     {k} -> {strip(v['tab_title'])}")
sys.exit(0 if sess else 3)
PY
  tag_ok=$?
  [ "$tag_ok" -eq 0 ] || return 2
  return 0
}

# Health of the whole picture. The honest signal is not "connections vs paths":
# pre-fix connections left listeners whose file is gone (orphans, harmless but they
# inflate the count), and a shared path can still leave the newest binder working.
# So report the three counts separately and only warn where it means something.
summary() {
  local n_conn n_path n_usable path orphans
  n_conn=$(tsshd_pids | wc -l)
  n_path=$(claimed_paths | wc -l)
  n_usable=0
  orphans=""
  for path in $(claimed_paths); do
    if [ -e "$path" ]; then
      n_usable=$((n_usable + 1))
    else
      orphans="$orphans $path"
    fi
  done
  echo "== connections: ${n_conn}, distinct paths: ${n_path}, paths with a file: ${n_usable}"
  if [ -n "$orphans" ]; then
    echo "   note: these paths have live listeners but no file (old shared-name orphans,"
    echo "         harmless — nothing can ever connect to them):${orphans}"
  fi
  if [ "$n_usable" -eq 0 ]; then
    echo "   !! no path the picker can open — bind p will show No Kitty Tab"
    return 2
  fi
  # Live listeners competing for one existing file is the real shadowing case.
  for path in $(claimed_paths); do
    [ -e "$path" ] || continue
    local n_listen
    n_listen=$(ss -xl 2>/dev/null | grep -c "[[:space:]]${path}[[:space:]]")
    if [ "${n_listen:-0}" -gt 1 ]; then
      echo "   !! ${n_listen} live listeners share ${path}: all but the newest are shadowed"
      return 2
    fi
  done
  return 0
}

# What the user actually sees: run the picker's own collection with its normal socket
# discovery and count how many agents failed to get a tab. This is the bottom line.
# An agent with no tmux session (a pi subagent run outside tmux) legitimately has no
# tab, so only agents that DO live in a session are counted as failures — otherwise
# every healthy run would report a problem.
end_to_end() {
  python3 - "$root/switch-pi-agent.py" <<'PY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("spa", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
strip = lambda s: re.sub(r"[\u200b\u200c\u2060\ufeff]", "", s)
agents = m.collect_agents()
no_tab = [a for a in agents if not a["tab_id"]]
real = [a for a in no_tab if a["session"] != "No Byobu"]
print(f"   agents={len(agents)}  without a kitty tab={len(no_tab)}"
      f" ({len(real)} of them inside a tmux session)")
for a in agents:
    if a["tab_id"]:
        mark = "OK "
    elif a["session"] == "No Byobu":
        mark = "-- "   # outside tmux, so no tab by design
    else:
        mark = "!! "
    print(f"   {mark}{a['session']}: {strip(a['tab_title'])}")
sys.exit(3 if real else 0)
PY
}

# The VS Code chain: VPS socket -> tunnel -> Mac listener. Deliberately NOT part of the
# exit status — a Mac without the launchd listener says nothing about the kitty tunnel,
# and making every run fail for it would train you to ignore the report.
check_vscode() {
  local socks on_disk live out
  socks=$(ls /tmp/vscode-open-remote-* 2>/dev/null || true)
  if [ -z "$socks" ]; then
    # Not a failure by itself: the old fixed-port forward still works
    echo "   no per-connection socket (old fixed-port forward, or nothing forwarded)"
  else
    on_disk=$(printf '%s\n' "$socks" | wc -l)
    live=0
    for f in $socks; do
      ss -xl 2>/dev/null | grep -q "[[:space:]]${f}[[:space:]]" && live=$((live + 1))
    done
    echo "   sockets on disk: ${on_disk}, with a live listener: ${live}"
  fi
  # The probe is the real answer: it walks exactly the paths the helper would use,
  # legacy fixed port included. The URL is not whitelisted, so no window opens.
  # No extra `!!` here: the helper already prefixes its own failure line.
  out=$(python3 "$root/tmux-open-vscode.py" --probe 2>&1)
  echo "   $out"
}

if [ "$MODE" = "--now" ]; then
  echo "== health of the connections alive right now:"
  [ -z "$(tsshd_pids)" ] && { echo "  no tsshd running"; exit 1; }
  worst=0
  # Check each PATH once: a shared path can only be judged as a whole.
  for path in $(claimed_paths); do
    rc=0; check_path "$path" || rc=$?
    [ "$rc" -eq 2 ] && worst=2
  done
  echo
  summary || worst=2
  if ls /tmp/mykitty-* >/dev/null 2>&1; then
    echo "== picker globs: $(ls /tmp/mykitty-* | tr '\n' ' ')"
  else
    echo "== !! nothing matches /tmp/mykitty-* — the picker WILL show No Kitty Tab"
    worst=2
  fi
  echo
  echo "== bind v (VS Code opener, separate from the exit status):"
  check_vscode
  echo
  echo "== what bind p would show right now:"
  end_to_end || worst=2
  exit $worst
fi

# ---- wait mode: catch the connection that a reconnect creates ----
# What counts as "a new connection" is deliberately not just a new PID: tsshd roams,
# so a network drop plus reconnect often lands back in the SAME tsshd process with the
# forward intact (observed: reconnect at 09:15 reused the tsshd from 09:15:38).
# So we snapshot the whole picture — pids plus every forwarded path's identity — and
# report as soon as any of it changes.
snapshot() {
  local pid inode path
  for pid in $(tsshd_pids); do
    echo "pid $pid"
    inode=$(readlink "/proc/$pid/fd/1" 2>/dev/null | tr -dc '0-9')
    [ -n "$inode" ] && echo "path $(path_of_inode "$inode") $inode"
  done
  # the file itself can be replaced while the listener set stays the same
  for path in /tmp/mykitty-*; do
    [ -e "$path" ] && stat -c 'file %n %i %Y' "$path" 2>/dev/null
  done | sort -u
}

before_state="$(snapshot)"
before=" $(tsshd_pids | tr '\n' ' ') "
echo "== tsshd alive now:${before}"
echo "== waiting up to ${WAIT}s for a NEW connection or forward ..."
# Order matters: start this FIRST, then reconnect on the Mac. A reconnect that already
# happened leaves nothing to observe — the snapshot is taken here, not retroactively.
echo "   (start this before reconnecting; if you already reconnected, use --now)"

deadline=$(( $(date +%s) + WAIT ))
newpid=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  if [ "$(snapshot)" != "$before_state" ]; then
    # Prefer a brand new tsshd when there is one; otherwise the same process re-bound.
    for pid in $(tsshd_pids); do
      case "$before" in *" $pid "*) continue ;; esac
      [ -d "/proc/$pid" ] && { newpid="$pid"; break; }
    done
    break
  fi
  sleep 1
done

if [ -z "$newpid" ] && [ "$(snapshot)" = "$before_state" ]; then
  echo "!! nothing changed in ${WAIT}s — no new tsshd and no new forward"
  exit 1
fi

sleep 2   # give the reverse forward a moment to finish binding
echo "== connection change detected:"
if [ -n "$newpid" ]; then
  echo "  new tsshd pid=$newpid"
  inode=$(readlink "/proc/$newpid/fd/1" 2>/dev/null | tr -dc '0-9')
  path=$(path_of_inode "$inode")
else
  # Same tsshd, so judge whatever paths it claims now.
  echo "  same tsshd process(es), forward changed"
  path=$(claimed_paths | tail -n 1)
fi
if [ -z "$path" ]; then
  echo "  !! forwarded nothing we can find — the reverse forward failed"
  exit 2
fi

echo
# bind v is the other half of "after a reconnect, does everything still work?" —
# the user-facing symptom is a VS Code window that never shows up
echo "== bind v (VS Code opener, separate from the exit status):"
check_vscode
echo

if check_path "$path"; then
  echo
  echo "PASS: reconnecting builds the path again and kitty answers through it."
  exit 0
fi
echo
echo "FAIL: see the !! lines above."
exit 2
