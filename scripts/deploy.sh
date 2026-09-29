#!/usr/bin/env bash
# 部署：tmux helper 软链到 ~/.local/bin，Pi 扩展软链到 ~/.pi/agent/extensions/；
# 改仓库即生效，无需重复部署
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p ~/.local/bin
ln -sfn "$root/switch-pi-agent.py" ~/.local/bin/switch-pi-agent.py
echo "已软链 ~/.local/bin/switch-pi-agent.py -> $root/switch-pi-agent.py"
ln -sfn "$root/tmux-pane-command.py" ~/.local/bin/tmux-pane-command.py
echo "已软链 ~/.local/bin/tmux-pane-command.py -> $root/tmux-pane-command.py"
mkdir -p ~/.pi/agent/extensions
ln -sfn "$root/kitty-tab-sync.ts" ~/.pi/agent/extensions/kitty-tab-sync.ts
echo "已软链 ~/.pi/agent/extensions/kitty-tab-sync.ts -> $root/kitty-tab-sync.ts"
