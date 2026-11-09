---
created_at: 2026-11-06
updated_at: 2026-11-09
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
- **Attempt**: Fixed shared `UdpSessionName=kitmux-vps` (same name for every run) with `UdpSessionAttach=yes`.
  - **Failure Reason**: `UdpSessionName` is a SHARED resource like a fixed socket path. When a second `tssh-vps` run attached, the first got `"Warning: Exited due to [vps] another client attached from <ip>"` and was detached — breaking reconnects. The per-connection unique-path doctrine applies to the session name too: give each script invocation its OWN name (`kitmux-vps-$$-$RANDOM`), which is stable across `--reconnect` restarts because `$$`/`$RANDOM` don't change while the shell lives.
- **Attempt**: Modify/fork tssh and tsshd source to add auto-reconnect.
  - **Failure Reason**: Unnecessary — the feature already exists upstream. Research against current trzsz/trzsz-ssh source found `--reconnect` (restarts client + re-login, foreground prompts, `-f` runs it background), and UDP-mode options `UdpSessionAttach` / `UdpSessionName` to re-attach to the same remote session after relaunch. No GitHub issue reports "-R not re-bound after long outage" as a bug; upstream treats re-login as the answer. Only a true hard limit justifies source work: if the outage exceeds `UdpAliveTimeout` (default `1w3d` ≈ 10 days), BOTH tssh and tsshd exit and nothing can rebuild — manual re-run is then the only option at any version.
- **Attempt**: Fixed shared forward paths (`/tmp/mykitty-remote`, one path for all connections) with `StreamLocalBindUnlink`.
  - **Failure Reason**: New connection unlinks the old socket file → all older connections become orphaned listeners (alive in kernel, file gone, unreachable). Fixed by per-connection unique paths.

## 3. Decision & Verified Solution
- **Chosen Approach**: Per-connection unique forward names + passive newest-socket glob on the VPS + tssh's OWN built-in auto-reconnect. Do NOT write a shell keepalive loop and do NOT fork tssh.
- **Key Changes**:
  - `scripts/tssh-vps.sh`: `FWD="/tmp/mykitty-remote-$$-${RANDOM}"`, `DOUBAO_FWD="/tmp/doubao-http-$$-${RANDOM}"`. No path fight, no orphaning.
  - VPS pickers: glob `/tmp/mykitty-*` / `/tmp/doubao-http-*`, sort newest-first, re-glob per request (no cached socket). They pick up a fresh tunnel automatically the moment it appears.
  - `scripts/verify-kitty-tunnel.sh`: `--now` for current health; `--wait` blocks until a reconnect happens, then health-checks the path.
  - `scripts/tssh-vps.sh` (Mac wrapper): the tunnel daemon now runs in the BACKGROUND with `-f -N` (fork + tunnel-only), then the wrapper execs a FOREGROUND interactive shell (`tssh HOST`). One command = two jobs. The daemon carries `--reconnect -o UdpSessionAttach=yes -o UdpSessionName=kitmux-vps-$$-${RANDOM}`: after a drop the client restarts, re-logs in, and a fresh login re-binds every `-R` forward. `UdpSessionAttach=yes` + a UNIQUE `UdpSessionName` = a restarted client re-attaches to the SAME remote session (its own) instead of spawning a new shell — and does not detach any other run. The per-connection unique path is safe here: each re-login makes a NEW `/tmp/mykitty-remote-*`, and the newest-glob picker self-heals. The shell and the daemon are two independent connections; exiting the shell does NOT kill the tunnel.
- **Minimal Code / Command Reference**:
  ```bash
  # in scripts/tssh-vps.sh: tunnel daemon background, then foreground shell.
  tssh --reconnect -f -N -o UdpSessionAttach=yes -o UdpSessionName="${UDP_SESSION}" \
    "${VSCODE_ARGS[@]}" -R "${FWD}:${sock}" -R "${DOUBAO_FWD}:127.0.0.1:8765" "$HOST" "$@"
  exec tssh "$HOST" "$@"   # foreground interactive shell, separate connection
  # UDP_SESSION="kitmux-vps-$$-${RANDOM}" (unique per run, stable across --reconnect)
  # Recovery check on VPS:
  ./scripts/verify-kitty-tunnel.sh --now
  ```
- **Online research facts (trzsz/trzsz-ssh, verified 2026-11-09):** `--reconnect` shipped in v0.1.25 (issue #216). Keepalive exists but is non-OpenSSH semantics: `ServerAliveInterval`/`ServerAliveCountMax` parsed, hardcoded defaults 10s/3, cannot be disabled, sends `keepalive@trzsz-ssh` global request only when no data received (issues #150, #166, #218). UDP roam options: `UdpMode yes|KCP|QUIC`, `UdpAliveTimeout` (default `1w3d`≈10 days), `UdpHeartbeatTimeout` (3s), `UdpReconnectTimeout` (15s), `UdpReconnectExitKey` (^d), `UdpProxyMode` (needed for reconnection), `UdpSessionAttach`, `UdpSessionName`. `ExitOnForwardFailure` is NOT in the supported-options README list (confirms earlier finding).

## 4. Hard Constraints for Future AI
- NEVER assume a network drop = dead tunnel. First check whether forward socket files exist and answer (`--now`); blips self-heal via kcp roam.
- NEVER rely on `ExitOnForwardFailure` in tssh — unsupported. NEVER expect roam to re-bind `-R` forwards; only a new login does.
- ALWAYS use per-connection unique socket paths (`$$-$RANDOM`); NEVER a fixed shared path with `StreamLocalBindUnlink`.
- To test a reconnect with `verify-kitty-tunnel.sh --wait`: START the script FIRST, then reconnect on the Mac. Reverse order sees nothing (snapshot taken after the change).
- Stale `tsshd` processes on the VPS are usually leftover orphaned listeners; kill only the ones with no live shell/pty attached, after user confirmation.
- NEVER wrap `tssh-vps.sh` in a shell `while true` loop — use tssh's own `--reconnect -f`. NEVER fork/modify tssh/tsshd to add auto-reconnect; the feature already exists upstream (v0.1.25+).
- NEVER use a FIXED `UdpSessionName` across runs — it is a shared resource and a second attach detaches the first ("another client attached"). Always make it unique per invocation (`kitmux-vps-$$-${RANDOM}`), which stays stable across `--reconnect` restarts.
- `--reconnect` needs tssh v0.1.25+; if the Mac runs older tssh, upgrade before relying on it. `UdpSessionAttach`/`UdpSessionName` only matter in UDP (KCP) mode, which this setup already uses.
- tssh has NO single-process "detach forwards but keep a shell" mode: `-f` backgrounds the whole invocation (verified in tssh source, `-f` implies `-n`), `-N` is the tunnel-only flag. So "one command, two jobs" is done by the wrapper: `-f -N` daemon + `exec tssh HOST` shell.
- HARD LIMIT that no version fixes: if an outage exceeds `UdpAliveTimeout` (~10 days default), both tssh and tsshd exit; re-running `tssh-vps.sh` by hand is then the ONLY recovery.
