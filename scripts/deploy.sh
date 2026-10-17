#!/usr/bin/env bash
# Deploy: symlink the tmux/Byobu config and helpers into HOME, and the Pi extension into
# ~/.pi/agent/extensions/; repo edits take effect at once, no redeploy needed
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

# On first migration, back up the user's real hand-written file (skip if it is already a symlink)
backup() {
  local path="$1"
  if [ -e "$path" ] && [ ! -L "$path" ]; then
    local bak="$path.bak.$(date +%Y%m%d%H%M%S)"
    mv "$path" "$bak"
    echo "Backed up $path -> $bak"
  fi
}

link() {
  local src="$1" dst="$2"
  # A .py helper without +x makes run-shell fail with exit 126, so make it executable
  [ -f "$src" ] && chmod +x "$src"
  mkdir -p "$(dirname "$dst")"
  backup "$dst"
  ln -sfn "$src" "$dst"
  echo "Linked $dst -> $src"
}

# tmux / Byobu config: the repo root tmux.conf is the one source of truth
# - native tmux reads ~/.tmux.conf on start
# - Byobu sources ~/.byobu/keybindings.tmux from profiles/tmuxrc line 35
link "$root/tmux.conf" "$HOME/.tmux.conf"
link "$root/tmux.conf" "$HOME/.byobu/keybindings.tmux"
# Platform extras: each OS sources only its own file, the symlink is harmless on the other
link "$root/tmux.darwin.conf" "$HOME/.tmux.darwin.conf"
link "$root/tmux.linux.conf" "$HOME/.tmux.linux.conf"

# Runtime helpers and the Pi extension
link "$root/switch-pi-agent.py" "$HOME/.local/bin/switch-pi-agent.py"
link "$root/tmux-pane-command.py" "$HOME/.local/bin/tmux-pane-command.py"
link "$root/tmux-pane-repo.py" "$HOME/.local/bin/tmux-pane-repo.py"
link "$root/tmux-pi-ack.py" "$HOME/.local/bin/tmux-pi-ack.py"
link "$root/tmux-open-vscode.py" "$HOME/.local/bin/tmux-open-vscode.py"
link "$root/kitty-tab-sync.ts" "$HOME/.pi/agent/extensions/kitty-tab-sync.ts"

# macOS only: the VS Code Remote opener (listens on 127.0.0.1:19998 for the VPS's
# `bind v` coming through the SSH RemoteForward tunnel). On Linux these files just sit
# there — tmux-open-vscode.py is used on the VPS, the listener/plist are not.
if [ "$(uname -s)" = "Darwin" ]; then
  plist_id="com.pi-kitmux.vscode-open-listener"
  plist_dst="$HOME/Library/LaunchAgents/$plist_id.plist"
  # Point the plist at this repo before installing it (the template has a placeholder)
  sed "s|REPO_ROOT|$root|g" "$root/scripts/$plist_id.plist" > "/tmp/$plist_id.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cp "/tmp/$plist_id.plist" "$plist_dst"
  launchctl unload "$plist_dst" 2>/dev/null || true
  launchctl load "$plist_dst"
  echo "Loaded launchd service: $plist_id (log: /tmp/pi-kitmux-vscode-listener.log)"
fi

cat <<'EOF'

Done. If tmux/Byobu is running, reload the config:
  tmux source-file ~/.tmux.conf   # or press F5 under Byobu
EOF
