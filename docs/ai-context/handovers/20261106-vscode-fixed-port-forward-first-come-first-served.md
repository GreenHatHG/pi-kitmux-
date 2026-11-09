---
created_at: 2026-11-06
updated_at: 2026-11-06
topic: vscode-fixed-port-forward-first-come-first-served
status: resolved
affected_modules: [tssh-vps.sh, tmux-open-vscode.py]
tags: [tssh, reverse-forward, tcp-port, eaddrinuse, vscode, tunnel]
---

# Fixed TCP Port 19998 Reverse Forward: First-Come-First-Served Orphans Other Connections

## 1. Context & Problem
`bind v` (open VS Code from a tmux pane on the VPS) silently failed for some tabs while working for others, and a brand-new SSH connection made it work again. Suspected cause: something in the tssh reconnect path.

## 2. Pitfalls & Failed Attempts (Gotchas - DO NOT REPEAT)
- **Attempt**: Blame "this tab's tunnel is dead".
  - **Failure Reason**: Measured live: all 5 kitty unix-socket forwards answered fine. Only the TCP port 19998 forward was single-owner. Wrong layer.
- **Attempt**: Assume the NEWEST connection owns the port.
  - **Failure Reason**: Re-measured: the OLDEST live connection (started earlier that day) owned 19998. It is first-come-first-served at login time, not newest-wins.
- **Root cause (source-verified, tssh 0.1.27)**: `-R 19998:127.0.0.1:19998` is a fixed port. At login, tsshd binds it once; later connections get `EADDRINUSE`, print one warning, and connect anyway (no `ExitOnForwardFailure` — unsupported in tssh). Also, after a roam, if a write to the client stream fails, tsshd closes the listener (`tsshd/forward.go:199-215` → `defer listener.Close()`), freeing the port — but roam never re-binds it. Result: one connection owns the port; every other tab freeloads on it; when the owner's transport goes mute, accept still succeeds but frames go nowhere → helper times out after 3 s → falls back to OSC 52 → VS Code never opens.
- **Attempt**: Reconnect everything to fix it.
  - **Failure Reason**: Only "works" because the fresh login happens to grab the now-free port. Same defect returns.

## 3. Decision & Verified Solution
- **Chosen Approach**: Same fix as the kitty sockets (commit `2c7c442`): give each connection an exclusive path instead of a shared scarce resource.
- **Key Changes**: `tssh-vps.sh`: replace `-R 19998:...` with a per-connection unix socket `/tmp/vscode-open-remote-$$-$RANDOM` forwarded to Mac port 19998. `tmux-open-vscode.py`: glob `/tmp/vscode-open-remote-*`, probe each, use the first that answers OK. Add the path to `scripts/verify-kitty-tunnel.sh` health checks.
- **Minimal Code / Command Reference**:
  ```bash
  # debug a bind-v failure directly (bypasses the keybinding):
  ~/.local/bin/tmux-open-vscode.py "$PWD"
  ```

## 4. Hard Constraints for Future AI
- NEVER share one TCP port across multiple tssh connections via `-R` — it is an exclusive resource; losers fail silently (warning only).
- ALWAYS pair a scarce forward with per-connection `$$-$RANDOM` naming plus a glob-and-probe client, mirroring the kitty pattern.
- ALWAYS check the forwarding design when a feature works in some tabs but not others after reconnects — it is usually shared-resource contention, not a broken tunnel.
