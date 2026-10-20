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
  if [ ! -e "$path" ]; then
    echo "     !! no file on disk: listener orphaned, unreachable forever"
    return 2
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

# Count of live connections vs paths the picker can actually use. Fewer usable paths
# than connections means some connections share one and are shadowed by it — a client
# cannot tell which one is behind a shared path, so this ratio is the honest signal.
# A shared path still leaves the picker (and usually the newest connection) working,
# so it is reported loudly but is not the same failure as a missing file.
summary() {
  local n_conn n_path
  n_conn=$(tsshd_pids | wc -l)
  n_path=$(claimed_paths | wc -l)
  echo "== connections: ${n_conn}, distinct paths: ${n_path}"
  if [ "${n_path:-0}" -eq 0 ]; then
    echo "   !! none of the ${n_conn} connection(s) forwarded a socket we can see"
    return 2
  fi
  [ "$n_conn" -gt "$n_path" ] || return 0
  echo "   !! $((n_conn - n_path)) connection(s) share a path with another one:"
  echo "      only the newest binder owns it; the rest are shadowed, but the path still works"
  return 2
}

# What the user actually sees: run the picker's own collection with its normal socket
# discovery and count how many agents failed to get a tab. This is the bottom line.
end_to_end() {
  python3 - "$root/switch-pi-agent.py" <<'PY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("spa", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
strip = lambda s: re.sub(r"[\u200b\u200c\u2060\ufeff]", "", s)
agents = m.collect_agents()
no_tab = [a for a in agents if not a["tab_id"]]
print(f"   agents={len(agents)}  without a kitty tab={len(no_tab)}")
for a in agents:
    mark = "OK " if a["tab_id"] else "!! "
    print(f"   {mark}{a['session']}: {strip(a['tab_title'])}")
PY
}

if [ "$MODE" = "--now" ]; then
  echo "== health of the connections alive right now:"
  [ -z "$(tsshd_pids)" ] && { echo "  no tsshd running"; exit 1; }
  worst=0
  # Check each PATH once: a shared path can only be judged as a whole.
  for path in $(claimed_paths); do
    check_path "$path" || worst=2
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
  echo "== what bind p would show right now:"
  end_to_end
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
echo "== waiting up to ${WAIT}s for a NEW connection or forward (reconnect from the Mac now) ..."

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

if check_path "$path"; then
  echo
  echo "PASS: reconnecting builds the path again and kitty answers through it."
  exit 0
fi
echo
echo "FAIL: see the !! lines above."
exit 2
