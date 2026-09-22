#!/usr/bin/env bash
# 部署：把脚本拷贝到 ~/.local/bin（~/.tmux.conf 弹窗实际执行的路径）
set -euo pipefail
cp -f "$(dirname "$0")/../switch-pi-agent.py" ~/.local/bin/switch-pi-agent.py
echo "已更新 ~/.local/bin/switch-pi-agent.py"
