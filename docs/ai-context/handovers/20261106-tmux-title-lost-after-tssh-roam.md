---
created_at: 2026-11-06
updated_at: 2026-11-06
topic: tmux-title-lost-after-tssh-roam
status: resolved
affected_modules: [tmux-title-tag.py, kitty-tab-sync.ts, tmux.conf]
tags: [tmux, title-cache, osc, nonce, tssh, roam, kitty]
---

# Kitty Tab Title Stale After tssh Roam (tmux title dedup cache)

## 1. Context & Problem
After a network drop + tssh roam reconnect, a kitty tab shows a stale pi status (e.g. stuck on `⏳` while tmux shows `✅`). tmux and kitty disagree on tab state, and it persists until another real state change.

## 2. Pitfalls & Failed Attempts (Gotchas - DO NOT REPEAT)
- **Root cause (source-verified)**: tmux dedups titles per client via `strcmp(title, c->title)` (`server-client.c:2627`). During the drop, tmux recomputed the title, updated its cache, but the OSC went into the dead connection and was lost. On roam the tmux client survives (tsshd keeps the pty; tmux never knows the network dropped) and the screen replays but the title does not. After that the string never changes → strcmp always hits cache → stuck forever.
- **Attempt**: `client-attached` hook to re-push the title on reconnect.
  - **Failure Reason**: Fires only for a brand-new client (`cmd-attach-session.c:166`). Roam never creates one — the pty stays open. And where it does fire (fresh attach), the title resends unconditionally anyway. Dead end.
- **Attempt**: Force a cache miss with `tmux set -u` then `set` back (via hook).
  - **Failure Reason**: Title sending is gated solely by the strcmp against the cache. Hook commands go on the same command queue; `options_push_changes` only sets redraw flags, rendered later after the whole queue drains. The intermediate `-u` state is NEVER rendered, final value equals cache → no OSC. `refresh-client -S` only forces the status line, not titles.
- **Attempt**: Poll/heartbeat with heavy traffic to force refreshes.
  - **Failure Reason**: A title OSC is ~1.9 KB; per-second resend ≈ 160 MB/day per tmux tab. Rejected for metered connections.
- **Attempt**: One-time fix of the tunnel socket paths (commit `2c7c442`).
  - **Failure Reason**: Necessary but insufficient — fixes picker/socket loss, not tmux's title cache swallowing an update.

## 3. Decision & Verified Solution
- **Chosen Approach**: Event-driven title re-push. When pi's status changes, `kitty-tab-sync.ts` flips the invisible `@pi_nonce` suffix between U+200B/U+200C and re-pushes the title ~5 s later (`RESEND_DELAY_MS`, overridable via `PI_TITLE_RESEND_MS`, values < 500 ms fall back to 5000). A lost title is re-pushed on the NEXT status change. This replaced the earlier per-minute-nonce design (see Section 2), which was live-tested then removed by user decision. `tmux-title-resync.py` + a tmux.conf watcher handle the new-login case; both explicitly CANNOT see a roam (roam reuses the same session and socket file).
- **Key Changes**: `kitty-tab-sync.ts` (delayed re-push + `@pi_nonce` flip, `PI_TITLE_RESEND_MS`), `tmux.conf` (`#{@pi_nonce}` after `set-titles-string`'s tag output, line 46), `tmux-title-resync.py` (new-login re-push; docstring states it cannot see a roam), tests `tests/test_tmux_conf.py` + `tests/test_title_resend.mjs` (node + throwaway tmux server + real pty, counts OSC).
- **Nonce safety**: picker only decodes between START/END; the nonce sits after END so decoded `host:session` is byte-identical. kitty renders the zero-width chars as nothing; visible text unchanged.
- **Known gap**: a title lost with no subsequent status event is not resent by the flip; the per-minute nonce (removed) was the abandoned fix for that gap.
- **Minimal Code / Command Reference**:
  ```
  set -g set-titles-string "...#{@pi_nonce}"   # nonce = last thing rendered
  ```

## 4. Hard Constraints for Future AI
- ALWAYS assume tmux is blind to a tssh roam: the client pty never closes, so there is NO reconnect event/hook. `tmux-title-resync.py` and the watcher cover only new logins; roam recovery relies on the next status-change re-push in `kitty-tab-sync.ts`.
- NEVER try to force a title resend by set/unset within one hook or command queue — the intermediate state is never rendered.
- NEVER re-introduce the per-minute nonce in `tmux-title-tag.py` without asking — it was implemented, verified, then removed by user decision (2026-10-23: "nonce 已完全撤掉"). The constant OSC traffic and tab-name override concern remain the reasons.
- Know the three loss categories: (1) OSC sent into dead connection (roam) — covered by next status-change re-push; (2) tmux client output backlog dropped (`TTY_BLOCK`, e.g. Ctrl-Z'd client) with no later status change — NOT covered by any shipped mechanism (the removed per-minute nonce was the candidate); (3) kitty-side freeze: a manually renamed tab sets `title_overridden` and kitty ignores ALL tmux titles until `kitty @ set-tab-title --none`. No re-push fixes category 3.
- After merging fixes: running processes still hold old logic — reload `~/.tmux.conf` (`tmux source-file`) and restart/reload pi in each pane.
