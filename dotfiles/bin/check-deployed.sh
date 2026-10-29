#!/usr/bin/env bash
# Verify every file deploy.sh manages is still a symlink to this repo.
# Guards against tools that replace config files via rename (breaking the link)
# -- pi's settings writer is the main suspect.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() {
  local dst="$1"
  if [ -L "$dst" ] && [ -e "$dst" ]; then
    return 0
  fi
  echo "BROKEN: $dst (expected symlink into $root)"
  fail=1
}
check "$HOME/.oh-my-zsh/custom/pi-kitmux.zsh"
check "$HOME/.gitconfig"
check "$HOME/.pi/agent/settings.json"
check "$HOME/.pi/agent/models.json"
check "$HOME/.pi/agent/web-search.json"
check "$HOME/.pi/agent/subagent_advisor.json"
for f in "$HOME"/.pi/agent/instructions/main/*.md \
         "$HOME"/.pi/agent/prompts/*.md \
         "$HOME"/.byobu/status "$HOME"/.byobu/color "$HOME"/.byobu/color.tmux; do
  [ -e "$f" ] && check "$f"
done
[ "$fail" = 0 ] && echo "all links ok"
exit $fail
