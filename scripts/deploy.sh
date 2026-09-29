#!/usr/bin/env bash
# 部署：把 tmux/Byobu 配置与 helper 软链到 HOME，把 Pi 扩展软链到 ~/.pi/agent/extensions/；
# 改仓库即生效，无需重复部署
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

# 首次迁移时，把用户手写的真实文件挪走备份（已是软链则不动）
backup() {
  local path="$1"
  if [ -e "$path" ] && [ ! -L "$path" ]; then
    local bak="$path.bak.$(date +%Y%m%d%H%M%S)"
    mv "$path" "$bak"
    echo "已备份 $path -> $bak"
  fi
}

link() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  backup "$dst"
  ln -sfn "$src" "$dst"
  echo "已软链 $dst -> $src"
}

# tmux / Byobu 配置：仓库根目录的 tmux.conf 是唯一事实源
# - 原生 tmux 启动时读取 ~/.tmux.conf
# - Byobu 由 profiles/tmuxrc 第 35 行 source ~/.byobu/keybindings.tmux
link "$root/tmux.conf" "$HOME/.tmux.conf"
link "$root/tmux.conf" "$HOME/.byobu/keybindings.tmux"

# 运行时 helper 与 Pi 扩展
link "$root/switch-pi-agent.py" "$HOME/.local/bin/switch-pi-agent.py"
link "$root/tmux-pane-command.py" "$HOME/.local/bin/tmux-pane-command.py"
link "$root/kitty-tab-sync.ts" "$HOME/.pi/agent/extensions/kitty-tab-sync.ts"

cat <<'EOF'

完成。若 tmux/Byobu 正在运行，重载配置：
  tmux source-file ~/.tmux.conf   # 或 Byobu 下按 F5
EOF
