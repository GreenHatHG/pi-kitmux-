---
created_at: 2026-11-06
updated_at: 2026-11-06
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
  - **Failure Reason**: Rejected by user preference and by design — the VPS is passive. Recovery doctrine is "reconnect from the Mac rebuilds everything"; a Mac-side keepalive wrapper around `tssh-vps.sh` is the only hands-off option and was deliberately not added.
- **Attempt**: Fixed shared forward paths (`/tmp/mykitty-remote`, one path for all connections) with `StreamLocalBindUnlink`.
  - **Failure Reason**: New connection unlinks the old socket file → all older connections become orphaned listeners (alive in kernel, file gone, unreachable). Fixed by per-connection unique paths.

## 3. Decision & Verified Solution
- **Chosen Approach**: Per-connection unique forward names + passive newest-socket glob on the VPS + explicit reconnect as the recovery action.
- **Key Changes**:
  - `scripts/tssh-vps.sh`: `FWD="/tmp/mykitty-remote-$$-${RANDOM}"`, `DOUBAO_FWD="/tmp/doubao-http-$$-${RANDOM}"`. No path fight, no orphaning.
  - VPS pickers: glob `/tmp/mykitty-*` / `/tmp/doubao-http-*`, sort newest-first, re-glob per request (no cached socket). They pick up a fresh tunnel automatically the moment it appears.
  - `scripts/verify-kitty-tunnel.sh`: `--now` for current health; `--wait` blocks until a reconnect happens, then health-checks the path.
- **Minimal Code / Command Reference**:
  ```bash
  # Mac-side keepalive wrapper (optional, not installed):
  while true; do ./tssh-vps.sh; sleep 3; done
  # Recovery check on VPS:
  ./scripts/verify-kitty-tunnel.sh --now
  ```

## 4. Hard Constraints for Future AI
- NEVER assume a network drop = dead tunnel. First check whether forward socket files exist and answer (`--now`); blips self-heal via kcp roam.
- NEVER rely on `ExitOnForwardFailure` in tssh — unsupported. NEVER expect roam to re-bind `-R` forwards; only a new login does.
- ALWAYS use per-connection unique socket paths (`$$-$RANDOM`); NEVER a fixed shared path with `StreamLocalBindUnlink`.
- To test a reconnect with `verify-kitty-tunnel.sh --wait`: START the script FIRST, then reconnect on the Mac. Reverse order sees nothing (snapshot taken after the change).
- Stale `tsshd` processes on the VPS are usually leftover orphaned listeners; kill only the ones with no live shell/pty attached, after user confirmation.
