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

# Same, but no chmod -- link() sets +x (helpers need it), config files must keep
# their mode or git records a spurious mode change on every deploy
symlink() {
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
# Platform extras: each OS sources only its own file, the symlink is harmless on the other
link "$root/tmux.darwin.conf" "$HOME/.tmux.darwin.conf"
link "$root/tmux.linux.conf" "$HOME/.tmux.linux.conf"

df="$root/dotfiles"

# byobu state-bar customization: module picker, colors
for f in status color color.tmux; do
  [ -f "$df/byobu/$f" ] && link "$df/byobu/$f" "$HOME/.byobu/$f"
done

# zsh: ~/.zshrc = pristine omz template + our 14-line pre-block spliced in before
# `source $ZSH/oh-my-zsh.sh` (theme/plugins/instant-prompt must run before omz).
# The template is never stored in the repo -- rebuilt from the installed omz.
# All the rest of the user config lives in custom.zsh, sourced by omz automatically.
zrc="$HOME/.zshrc"
tpl="$HOME/.oh-my-zsh/templates/zshrc.zsh-template"
if [ -f "$tpl" ]; then
  backup "$zrc"
  # splice: template up to the source line, then our pre-block, then the rest
  awk -v pre="$df/zsh/zshrc.pre" '
    /source \$ZSH\/oh-my-zsh\.sh/ && !done { while ((getline line < pre) > 0) print line; close(pre); done=1 }
    { print }' "$tpl" > "/tmp/zshrc.pi-kitmux"
  mv "/tmp/zshrc.pi-kitmux" "$zrc"
  echo "Built $zrc from omz template + dotfiles/zsh/zshrc.pre"
else
  echo "!! oh-my-zsh not found, skipping ~/.zshrc build (run again after omz install)"
fi
# custom config: sourced automatically by omz from $ZSH_CUSTOM/*.zsh
link "$df/zsh/custom.zsh" "$HOME/.oh-my-zsh/custom/pi-kitmux.zsh"
# p10k: NOT stored. The wizard's choices are recorded in dotfiles/notes/
# p10k-wizard-choices.txt; run `p10k configure` once on the new box.

# Runtime helpers and the Pi extension
link "$root/switch-pi-agent.py" "$HOME/.local/bin/switch-pi-agent.py"
link "$root/tmux-title-tag.py" "$HOME/.local/bin/tmux-title-tag.py"
link "$root/tmux-title-resync.py" "$HOME/.local/bin/tmux-title-resync.py"
link "$root/tmux-pane-command.py" "$HOME/.local/bin/tmux-pane-command.py"
link "$root/tmux-pane-repo.py" "$HOME/.local/bin/tmux-pane-repo.py"
link "$root/tmux-pi-ack.py" "$HOME/.local/bin/tmux-pi-ack.py"
link "$root/tmux-open-vscode.py" "$HOME/.local/bin/tmux-open-vscode.py"
link "$root/kitty-tab-sync.ts" "$HOME/.pi/agent/extensions/kitty-tab-sync.ts"

# Pi agent config: instructions are read from ~/.pi/agent/instructions/main/, skills
# from ~/.pi/agent/skills/ or ~/.agents/skills/ (the latter is shared with other
# harnesses, so a skill synced here also shows up for Claude Code)
for f in "$df"/instructions/*.md; do
  symlink "$f" "$HOME/.pi/agent/instructions/main/$(basename "$f")"
done
for d in "$df"/skills/*/; do
  symlink "${d%/}" "$HOME/.agents/skills/$(basename "${d%/}")"
done
# pi's own json config. pi writes these with temp-file + rename, which turns the symlink
# into a plain file and quietly stops repo edits from landing -- rerun deploy.sh to heal
# (backup() moves the drifted file aside first).
for f in settings models web-search subagent_advisor; do
  [ -f "$df/pi/$f.json" ] && symlink "$df/pi/$f.json" "$HOME/.pi/agent/$f.json"
done

# macOS only: tssh wrapper that resolves the live kitty remote-control socket
# (its name always embeds kitty's PID, so it changes per kitty restart) and
# opens the reverse tunnel with tssh itself — no autossh, no cleanup: the
# tunnel lives and dies with the connection. On Linux this file just sits there.
if [ "$(uname -s)" = "Darwin" ]; then
  link "$root/scripts/tssh-vps.sh" "$HOME/.local/bin/tssh-vps"
fi

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
