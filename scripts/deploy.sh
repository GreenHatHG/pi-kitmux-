#!/usr/bin/env bash
# 部署：switch-pi-agent.py 软链到 ~/.local/bin（~/.tmux.conf 弹窗实际执行的路径），
# kitty-tab-sync.ts 软链到 ~/.pi/agent/extensions/（pi 扩展加载目录），改仓库即生效，无需重复部署
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
ln -sfn "$root/switch-pi-agent.py" ~/.local/bin/switch-pi-agent.py
echo "已软链 ~/.local/bin/switch-pi-agent.py -> $root/switch-pi-agent.py"
mkdir -p ~/.pi/agent/extensions
ln -sfn "$root/kitty-tab-sync.ts" ~/.pi/agent/extensions/kitty-tab-sync.ts
echo "已软链 ~/.pi/agent/extensions/kitty-tab-sync.ts -> $root/kitty-tab-sync.ts"
