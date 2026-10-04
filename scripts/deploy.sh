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

# Runtime helpers and the Pi extension
link "$root/switch-pi-agent.py" "$HOME/.local/bin/switch-pi-agent.py"
link "$root/tmux-pane-command.py" "$HOME/.local/bin/tmux-pane-command.py"
link "$root/tmux-pane-repo.py" "$HOME/.local/bin/tmux-pane-repo.py"
link "$root/tmux-pi-ack.py" "$HOME/.local/bin/tmux-pi-ack.py"
link "$root/kitty-tab-sync.ts" "$HOME/.pi/agent/extensions/kitty-tab-sync.ts"

cat <<'EOF'

Done. If tmux/Byobu is running, reload the config:
  tmux source-file ~/.tmux.conf   # or press F5 under Byobu
EOF
