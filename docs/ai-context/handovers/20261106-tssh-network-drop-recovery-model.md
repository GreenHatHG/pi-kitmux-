---
created_at: 2026-11-06
updated_at: 2026-11-12
topic: tssh-network-drop-recovery-model
status: resolved
affected_modules: [tssh-vps.sh, verify-kitty-tunnel.sh, pi-extension-doubao-translate]
tags: [tssh, tsshd, kcp, roam, reverse-forward, unix-socket, recovery]
---

# tssh Network-Drop Recovery Model (roam vs. full disconnect)

## 1. Context & Problem
Mac runs `tssh-vps.sh` to the VPS with `-R` reverse forwards (unix sockets for kitty pickers, doubao HTTP, plus TCP port 19998). After a network drop the VPS side sometimes stops working while the Mac side looks healthy. Need to know what self-heals and what needs manual action.

## 2. Pitfalls & Failed Attempts (Gotchas - DO NOT REPEAT)
- **Attempt**: Assume a network drop kills the tunnel every time.
  - **Failure Reason**: Two different failure levels exist. Case 1 (blip, seconds–minutes): kcp (UDP) transport heals, the SAME tsshd process is reused, the `-R` unix-socket forward survives, socket file never disappears. Verified live 2026-11-06: after a blip, `/tmp/doubao-http-<pid>-<rand>` sockets existed and health check passed with no action.
  - Case 2 (long outage): tssh client on the Mac times out and exits. tsshd removes the forward socket file. Recovery requires re-running `tssh-vps.sh` on the Mac. tsshd does NOT re-establish `-R` forwards on its own.
- **Attempt**: Rely on `ExitOnForwardFailure=yes` in `tssh-vps.sh` to catch bind failures.
  - **Failure Reason**: Verified against tssh 0.1.27 source (`forward_tcp.go:146-162`): the option is parsed into an options map and NEVER used. Bind failure prints one warning and continues. The flag is decoration.
- **Attempt**: Expect roam (tsshd reconnect to same process) to re-bind forwards.
  - **Failure Reason**: Source-verified (`main.go:405` → `forward.go:325`): forwards bind once at login only. `SendRequest` for re-sending global requests returns "not supported yet". Only a fresh login re-binds. Exception: already-established unix-socket forward streams DO survive roam (`waitUntilReconnected`, `forward.go:321-334`); TCP listen ports do not reliably survive.
- **Attempt**: Keepalive/heartbeat from the VPS side.
  - **Failure Reason**: Rejected by user preference and by design — the VPS is passive. Recovery doctrine is "reconnect from the Mac rebuilds everything".
- **Attempt**: Manual Mac-side keepalive wrapper (`while true; do ./tssh-vps.sh; sleep 3; done`).
  - **Failure Reason**: Now obsolete. This was the old hands-off answer, but it was a poor shell loop. tssh has a BUILT-IN `--reconnect` (v0.1.25+): after a drop it restarts the client and re-logs in, and a fresh login re-binds every `-R` forward. The loop duplicated a native feature and was never installed.
- **Attempt**: Per-run background daemon with `UdpSessionAttach=yes` + unique `UdpSessionName` (commit 9b698ed).
  - **Failure Reason**: Two source-verified problems (tssh 0.1.27, tsshd 0.1.10, researched 2026-11-10). (1) `-f --reconnect` spawns a supervisor (TRZSZ-SSH-BG-MONITOR) that respawns the client forever and the client never exits — every `tssh-vps` run left one daemon behind; 7 orphaned tsshd sessions observed live on 2026-11-09, each holding 3 stale socket files for up to `UdpAliveTimeout` (default ~10 days). (2) In attach mode `SIGTERM` to the client is a **Detach** (tssh `ssh.go` forceExit: `attachMode && !wantExit` → `client.Detach()`), not a close — the remote tsshd keeps all forwards until the alive timeout. And a `-N` client has no shell session to re-attach to, so attach bought nothing.
- **Attempt**: Shell-bound daemon ("Option B"): drop `-f`, run the tunnel as a background child of the wrapper with `trap 'kill $PID' EXIT`.
  - **Failure Reason**: Rejected on user requirements 2026-11-10. The trap needs the shell as a non-exec'd child (an `exec tssh` never fires the trap), and the wrapper lives only as long as the VPS shell — so a long outage kills the shell, the trap kills the daemon, and the daemon's `--reconnect` dies exactly when it is needed. The user's scenario (lid close → sleep → wake → doubao auto-recovers with kitty/tmux still open) needs a daemon that survives the shell and self-heals on wake. Also fixed-path sockets stay impossible: tsshd honors `StreamLocalBindUnlink yes` for stale FILES (v0.1.7+, tsshd/forward.go unlinkStaleUnixSocket) but can never take a path still held by a live listener.
- **Attempt**: Modify/fork tssh and tsshd source to add auto-reconnect.
  - **Failure Reason**: Unnecessary — the feature already exists upstream. Research against current trzsz/trzsz-ssh source found `--reconnect` (restarts client + re-login, foreground prompts, `-f` runs it background), and UDP-mode options `UdpSessionAttach` / `UdpSessionName` to re-attach to the same remote session after relaunch. No GitHub issue reports "-R not re-bound after long outage" as a bug; upstream treats re-login as the answer. Only a true hard limit justifies source work: if the outage exceeds `UdpAliveTimeout` (default `1w3d` ≈ 10 days), BOTH tssh and tsshd exit and nothing can rebuild — manual re-run is then the only option at any version.
- **Attempt**: Fixed shared forward paths (`/tmp/mykitty-remote`, one path for all connections) with `StreamLocalBindUnlink`.
  - **Failure Reason**: New connection unlinks the old socket file → all older connections become orphaned listeners (alive in kernel, file gone, unreachable). Fixed by per-connection unique paths.
- **Attempt**: Singleton guard that ONLY pgrep-checks `UdpSessionName=kitmux-tunnel-daemon` and reuses on match (the 2026-11-10 guard).
  - **Failure Reason**: pgrep proves process existence, not tunnel health. The `--reconnect` supervisor never exits, so a daemon stuck in a failed-relogin loop still matched; live symptom 2026-11-12: `tssh-vps` printed "tunnel daemon already running; reusing it" while NO connection was established — silent no-tunnel session. Fixed by an lsof connection probe (see Section 3).
- **Attempt**: Test harnesses whose OWN cmdline contains the marker string `UdpSessionName=kitmux-tunnel-daemon` (e.g. a heredoc that pgreps for the marker).
  - **Failure Reason**: `pgrep -f` matches the harness's own `bash -c` cmdline; tests self-kill or self-match. Decoy daemons must be exec'd from a FILE (e.g. `exec -a "tssh -o UdpSessionName=..." sleep 300` via a script file), never inline in the harness cmdline.
- **Attempt**: Rely on `lsof` unconditionally in the guard's health probe (fail closed).
  - **Failure Reason**: The Debian VPS sandbox has no `lsof`; fail-closed would kill+restart the daemon on EVERY run — worse than the original bug. Fail open (reuse) when `lsof` is missing; macOS ships `/usr/sbin/lsof`.

## 3. Decision & Verified Solution
- **Chosen Approach**: Per-connection unique forward names + passive newest-socket glob on the VPS + tssh's OWN built-in auto-reconnect + a SINGLETON background daemon. Do NOT write a shell keepalive loop, do NOT fork tssh, and do NOT bind the tunnel's life to the interactive shell. Lifecycle requirements (user-confirmed): tunnel must keep working (1) while kitty+pi+tmux stay open, and (2) after lid close/sleep/wake — doubao translation must auto-recover with no manual action; tunnel is NOT needed when no Mac window is open.
- **Key Changes**:
  - `scripts/tssh-vps.sh`: `FWD="/tmp/mykitty-remote-$$-${RANDOM}"`, `DOUBAO_FWD="/tmp/doubao-http-$$-${RANDOM}"`. No path fight, no orphaning.
  - VPS pickers: glob `/tmp/mykitty-*` / `/tmp/doubao-http-*`, sort newest-first, re-glob per request (no cached socket). They pick up a fresh tunnel automatically the moment it appears.
  - `scripts/verify-kitty-tunnel.sh`: `--now` for current health; `--wait` blocks until a reconnect happens, then health-checks the path.
  - `scripts/tssh-vps.sh` (Mac wrapper, revised 2026-11-10, guard hardened 2026-11-12, commit b654eaa): **singleton daemon with health-checked reuse** — the wrapper pgrep-guards on the inert marker `UdpSessionName=kitmux-tunnel-daemon`, then probes each matched pid with `lsof -a -p <pid> -i -n -P` for `ESTABLISHED|UDP` (TCP covers QUIC/plain, UDP covers kcp): healthy → reuse; alive but connection-less → `stop_daemon()` (process-group kill, extracted and shared with `--stop-tunnel`) then fall through to a fresh start; no match → fresh start. Missing `lsof` → fail OPEN (reuse), so the guard can never become a kill-restart loop. **No UdpSessionAttach/UdpSessionName-for-attach**: attach turns SIGTERM into a detach (remote tsshd lingers with all forwards) and a `-N` client has no session to attach to; plain `--reconnect` re-logins and re-binds all `-R` forwards by itself. `UdpAliveTimeout=600` is a CLIENT-side value sent to tsshd at connect (there is no server-side knob) and caps any client-less session at 10 minutes. The shell is a separate foreground `tssh HOST`; exiting it does not kill the shared daemon — Mac reboot/logout does, and remote residue self-expires ≤10 min. Sleep/lid-close scenario: on wake the surviving daemon's `--reconnect` supervisor re-logins and re-binds fresh forwards; VPS pickers re-glob and pick up the new sockets with no manual action.
- **Minimal Code / Command Reference**:
  ```bash
  # scripts/tssh-vps.sh: health-checked singleton guard (2026-11-12, b654eaa)
  daemon_healthy() {
    command -v lsof >/dev/null 2>&1 || return 0   # fail OPEN when lsof missing
    local pid
    for pid in $(pgrep -f "UdpSessionName=${UDP_SESSION}"); do
      lsof -a -p "$pid" -i -n -P 2>/dev/null | grep -qE 'ESTABLISHED|UDP' && return 0
    done
    return 1
  }
  if pgrep -f "UdpSessionName=${UDP_SESSION}" >/dev/null 2>&1; then
    if daemon_healthy; then exec tssh "$HOST" "$@"; fi
    echo "tssh-vps: tunnel daemon alive but not connected; restarting it" >&2
    stop_daemon   # process-group kill, shared with --stop-tunnel
  fi
  # ... then the usual: resolve_socket + tssh --reconnect -f -N ... + exec tssh HOST
  # Manual stop: tssh-vps --stop-tunnel  (kills the whole process group)
  # Recovery check on VPS: ./scripts/verify-kitty-tunnel.sh --now
  ```
  Verified by adversarial verifier 2026-11-12 (fake-lsof harness): healthy TCP → reuse; UDP-only → reuse; connection-less → restart + group kill + fresh start; supervisor+client pair (first pid socket-less, second connected) → reuse (loop scans ALL pids); lsof missing → reuse; --stop-tunnel idempotent.
- **Online research facts (trzsz/trzsz-ssh, verified 2026-11-09):** `--reconnect` shipped in v0.1.25 (issue #216). Keepalive exists but is non-OpenSSH semantics: `ServerAliveInterval`/`ServerAliveCountMax` parsed, hardcoded defaults 10s/3, cannot be disabled, sends `keepalive@trzsz-ssh` global request only when no data received (issues #150, #166, #218). UDP roam options: `UdpMode yes|KCP|QUIC`, `UdpAliveTimeout` (default `1w3d`≈10 days), `UdpHeartbeatTimeout` (3s), `UdpReconnectTimeout` (15s), `UdpReconnectExitKey` (^d), `UdpProxyMode` (needed for reconnection), `UdpSessionAttach`, `UdpSessionName`. `ExitOnForwardFailure` is NOT in the supported-options README list (confirms earlier finding).

## 4. Hard Constraints for Future AI
- NEVER assume a network drop = dead tunnel. First check whether forward socket files exist and answer (`--now`); blips self-heal via kcp roam.
- NEVER rely on `ExitOnForwardFailure` in tssh — unsupported. NEVER expect roam to re-bind `-R` forwards; only a new login does.
- ALWAYS use per-connection unique socket paths (`$$-$RANDOM`); NEVER a fixed shared path with `StreamLocalBindUnlink`.
- To test a reconnect with `verify-kitty-tunnel.sh --wait`: START the script FIRST, then reconnect on the Mac. Reverse order sees nothing (snapshot taken after the change).
- Stale `tsshd` processes on the VPS are usually leftover orphaned listeners; kill only the ones with no live shell/pty attached, after user confirmation.
- NEVER wrap `tssh-vps.sh` in a shell `while true` loop — use tssh's own `--reconnect -f`. NEVER fork/modify tssh/tsshd to add auto-reconnect; the feature already exists upstream (v0.1.25+).
- NEVER use `UdpSessionAttach=yes` (or attach-mode `UdpSessionName`) on the tunnel daemon: SIGTERM then detaches instead of closing, and the remote tsshd lingers with every forward until the alive timeout. Source-verified in tssh `ssh.go` forceExit (attachMode && !wantExit → Detach).
- NEVER assume a fixed `UdpSessionName` is safe just because attach is off — with attach ON it is a shared resource and a second attach detaches the first ("another client attached"). Keep `UdpSessionName=kitmux-tunnel-daemon` ONLY as an inert pgrep marker.
- NEVER spawn a tunnel daemon per `tssh-vps` run: `-f --reconnect` respawns the client forever, so per-run daemons stack up as orphans. The wrapper must pgrep-guard and reuse the existing daemon.
- Client-side `UdpAliveTimeout` (e.g. `600`) is the ONLY knob that caps a client-less remote session's life — there is no server-side tsshd timeout flag. Set it on the daemon.
- To kill the daemon fully, kill the PROCESS GROUP (`tssh-vps --stop-tunnel`): the `-f --reconnect` monitor (TRZSZ-SSH-BG-MONITOR) respawns the client, so killing the monitor alone leaves a live child.
- NEVER treat a pgrep match as proof the tunnel works — ALWAYS health-probe the daemon's network connections (lsof ESTABLISHED/UDP) before reusing it; the supervisor never exits, so a stuck daemon still matches pgrep forever.
- NEVER fail closed when `lsof` is missing in the health probe: fail open (reuse) or the guard restarts the daemon on every run.
- NEVER put the marker string `UdpSessionName=kitmux-tunnel-daemon` in a TEST harness's own cmdline (heredocs included) — `pgrep -f` self-matches; decoys must be exec'd from a file.
- Accept the documented trade-off: a healthy daemon sampled mid-reconnect can be misjudged stale and restarted — harmless, a fresh login creates new per-run forward paths.
- `--reconnect` needs tssh v0.1.25+; if the Mac runs older tssh, upgrade before relying on it.
- tssh has NO single-process "detach forwards but keep a shell" mode: `-f` backgrounds the whole invocation (verified in tssh source, `-f` implies `-n`), `-N` is the tunnel-only flag. So "one command, two jobs" is done by the wrapper: `-f -N` daemon + `exec tssh HOST` shell.
- HARD LIMIT that no version fixes: if an outage exceeds `UdpAliveTimeout` (~10 days default), both tssh and tsshd exit; re-running `tssh-vps.sh` by hand is then the ONLY recovery.
