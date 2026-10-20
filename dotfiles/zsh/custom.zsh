# pi-kitmux custom config: sourced automatically by oh-my-zsh from $ZSH_CUSTOM/*.zsh
# (after plugins, before the theme). Everything user-defined lives here.

export LANG=en_US.UTF-8

. "$HOME/.local/bin/env"
# bun completions
[ -s "$HOME/.bun/_bun" ] && source "$HOME/.bun/_bun"
# bun
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"
bb() {
  # KITTY_WINDOW_ID 过不了 mosh/ssh，退回用 TTY 名当唯一标识
  local session_name="${1:-win-${KITTY_WINDOW_ID:-${TTY##*/}}}"
  byobu new-session -A -s "$session_name"
}
spi() {
    local dirs=()
    local passthru=()
    local appends=()
    local arg f
    # 解析参数：-- 之前的是额外可写目录，-- 之后透传给 pi
    local seen_dd=0
    for arg in "$@"; do
        if [ "$arg" = "--" ]; then
            seen_dd=1
            continue
        fi
        if [ "$seen_dd" = 0 ]; then
            dirs+=("-w" "${~arg}")   # ${~arg} 展开 ~ 路径
        else
            passthru+=("$arg")
        fi
    done
    # KITTY_WINDOW_ID 过不了 mosh/ssh，退回用 TTY 名当唯一标识（mosh 重连后 pty 不变，名字稳定）
    local session_name="win-${KITTY_WINDOW_ID:-${TTY##*/}}"
    local spa="$HOME/.pi/agent/instructions/main"
    for f in ${~spa}/*.md(N); do
        appends+=(--append-system-prompt "$f")
    done
    local cmd="PI_BASH_GUARD_HINT=0 PI_WATCHDOG=\"timeout=5 mode=keep\" pi ${appends[*]} ${passthru[*]}"
    if [ -n "$BYOBU_BACKEND" ] || [ -n "$TMUX" ]; then
        eval "$cmd"
    else
        # 先在后台把 session 建好，别急着 attach
        byobu new-session -A -s "$session_name" -d
        # 等 tmux server 真正就绪，最多 5 秒，不然 send-keys 会静默丢命令
        local i=0
        until tmux has-session -t "$session_name" 2>/dev/null; do
            sleep 0.1
            (( i++ > 50 )) && break
        done
        # 显式指定目标，防止命令打到别的 session
        tmux send-keys -t "$session_name" "$cmd" C-m
        byobu attach -t "$session_name" || byobu select-session -t "$session_name"
    fi
}
# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh
gwa() {
    local branch="$1"
    local base="${2:-main}"
    if [ -z "$branch" ]; then
        echo "用法: gwa <新分支名> [基础分支(默认main)]"
        return 1
    fi
    local root_dir
    root_dir=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)") || return 1
    local wt_path="$root_dir/.worktrees/$branch"
    git worktree add -b "$branch" "$wt_path" "$base" && {
        # 如果在 Byobu/Tmux 中，新建一个以分支命名的 Window 并进入
        if [ -n "$BYOBU_WINDOW" ] || [ -n "$TMUX" ]; then
            tmux new-window -n "wt:$branch" -c "$wt_path"
        else
            cd "$wt_path"
        fi
    }
}
# 2. 展示列表并通过 fzf 快速跳转
# 用法: gwl
gwl() {
    local selected
    selected=$(git worktree list | fzf --height 40% --reverse --preview 'git -C {1} status -s' | awk '{print $1}')
    if [ -n "$selected" ]; then
        cd "$selected" || return
    fi
}
# 3. 快速合并回 main 并清理 Worktree
# 用法: 在 worktree 目录内直接执行 gwm
gwm() {
    local current_wt branch main_wt main_wt_branch
    current_wt=$(git rev-parse --show-toplevel) || return 1
    branch=$(git branch --show-current)
    if [ -z "$branch" ]; then
        echo "错误: 当前处于 detached HEAD，无法确定分支名"
        return 1
    fi
    if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
        echo "当前已在主分支工作区，请在特性分支的 worktree 运行 gwm"
        return 1
    fi
    # 找到主仓库工作树路径（第一条记录即为主目录）
    main_wt=$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -n 1)
    main_wt_branch=$(git -C "$main_wt" rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [ "$main_wt_branch" != "main" ]; then
        echo "错误: 主 worktree 当前在 [$main_wt_branch]，请先切换到 main"
        return 1
    fi
    # 确保两个工作区都没有未提交改动
    if [ -n "$(git -C "$main_wt" status --porcelain)" ]; then
        echo "错误: 主 worktree 有未提交改动，请先处理"
        return 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "错误: 当前工作区有未提交的代码，请先 commit 或 stash"
        return 1
    fi
    echo "==> 在特性分支 [$branch] 上 rebase main..."
    if ! git rebase main; then
        echo "rebase 冲突或失败，请手动解决后运行: git rebase --continue"
        echo "解决完成后再次运行 gwm"
        return 1
    fi
    if ! git merge-base --is-ancestor main "$branch"; then
        echo "错误: rebase 后 main 仍不是 [$branch] 的祖先，无法 ff-only"
        return 1
    fi
    echo "==> 将 [$branch] fast-forward 到 [$main_wt] 的 main 分支..."
    cd "$main_wt" || return 1
    if ! git merge --ff-only "$branch"; then
        echo "ff-only 合并失败，分支可能已分叉，请检查"
        cd "$current_wt"
        return 1
    fi
    echo "==> 合并成功，正在清理 worktree 和分支..."
    if ! git worktree remove "$current_wt"; then
        echo "警告: worktree 移除失败，请手动运行: git worktree remove \"$current_wt\""
    fi
    git branch -d "$branch" || echo "警告: 分支删除失败，可手动运行: git branch -d \"$branch\""
    echo "==> 完成清理！"
}
# 4. 快速交互式删除 Worktree (含分支删除)
# 用法: gwd
gwd() {
    local selection selected_path selected_branch main_branch
    # 排除第一行主工作区，用 fzf 交互选择
    selection=$(git worktree list | tail -n +2 | fzf --height 40% --reverse --prompt="选择要删除的 Worktree: ")
    [ -z "$selection" ] && return 0
    selected_path=$(echo "$selection" | awk '{print $1}')
    selected_branch=$(echo "$selection" | awk '{print $3}' | tr -d '[]')
    # 自动识别主分支名称 (main 或 master)
    main_branch="main"
    if ! git show-ref --verify --quiet refs/heads/main; then
        if git show-ref --verify --quiet refs/heads/master; then
            main_branch="master"
        fi
    fi
    # 1. 检查是否有未提交的工作区修改
    local dirty_status
    dirty_status=$(git -C "$selected_path" status --porcelain 2>/dev/null)
    # 2. 检查是否有未合并到主分支的 commit
    local unmerged_commits=""
    if [ -n "$selected_branch" ]; then
        unmerged_commits=$(git log "${selected_branch}" --not "${main_branch}" --oneline 2>/dev/null)
    fi
    local is_safe=true
    # 风险提示 1：有未提交文件
    if [ -n "$dirty_status" ]; then
        is_safe=false
        printf "\033[33m⚠️  警告: 工作区包含未提交的代码修改:\033[0m\n"
        git -C "$selected_path" status -s
        echo ""
    fi
    # 风险提示 2：有未合并的提交
    if [ -n "$unmerged_commits" ]; then
        is_safe=false
        printf "\033[33m⚠️  警告: 分支 [%s] 还有未合并到 [%s] 的提交:\033[0m\n" "$selected_branch" "$main_branch"
        echo "$unmerged_commits" | head -n 5
        local count
        count=$(echo "$unmerged_commits" | wc -l | tr -d ' ')
        [ "$count" -gt 5 ] && echo "... (共 $count 个未合并 Commit)"
        echo ""
    fi
    # 根据体检结果输出不同的提示
    local prompt_msg=""
    if [ "$is_safe" = true ]; then
        prompt_msg="✅ [已合并/安全] 确认删除分支 [$selected_branch] 及 Worktree? (y/N) "
    else
        prompt_msg="\033[31m💥 [存在未合并改动] 强制删除将丢失代码！确认强制销毁? (y/N) \033[0m"
    fi
    printf "%b" "$prompt_msg"
    local reply
    if [ -n "$ZSH_VERSION" ]; then
        read -k 1 -r reply
    else
        read -n 1 -r reply
    fi
    echo
    # 执行删除
    if [[ $reply =~ ^[Yy]$ ]]; then
        if [ "$is_safe" = true ]; then
            git worktree remove "$selected_path"
            [ -n "$selected_branch" ] && git branch -d "$selected_branch"
            echo "==> ✅ 已安全删除 Worktree 与分支: $selected_branch"
        else
            git worktree remove "$selected_path" --force
            [ -n "$selected_branch" ] && git branch -D "$selected_branch"
            echo "==> 🗑️ 已强制删除 Worktree 与分支: $selected_branch"
        fi
    else
        echo "==> 操作已取消"
    fi
}
export COLORTERM=truecolor
pbcopy() {
  if [ -n "$TMUX" ]; then
    tmux load-buffer -w "${1:--}"
  else
    local data
    data=$(cat "${1:--}" | base64 | tr -d '\n')
    printf "\033]52;c;%s\007" "$data"
  fi
}

# 跨平台取文件字节数：GNU stat (Linux) 用 -c %s，BSD stat (macOS) 用 -f %z
_file_bytes() {
  stat -c %s "$1" 2>/dev/null || stat -f %z "$1"
}

towebp() {
  if [ $# -eq 0 ]; then
    echo "用法: towebp <图片路径...>"
    return 1
  fi

  # cwebp 没装就直接说清楚，别让循环里每条都报一次失败
  if ! command -v cwebp >/dev/null 2>&1; then
    echo "找不到 cwebp。macOS: brew install webp；Debian/Ubuntu: sudo apt install webp"
    return 1
  fi

  for file in "$@"; do
    if [ -f "$file" ]; then
      local out="${file%.*}.webp"
      # 原图字节数
      local orig_bytes=$(_file_bytes "$file")

      # -quiet 屏蔽 cwebp 自带的冗余日志
      if cwebp -quiet -q 80 "$file" -o "$out"; then
        local new_bytes=$(_file_bytes "$out")

        # 换算单位并输出结果
        awk -v o="$orig_bytes" -v n="$new_bytes" -v name="$(basename "$file")" '
          function hr(b) {
            if (b >= 1048576) return sprintf("%.2f MB", b / 1048576);
            if (b >= 1024)    return sprintf("%.1f KB", b / 1024);
            return sprintf("%d B", b);
          }
          BEGIN {
            pct = (o > 0) ? ((o - n) / o) * 100 : 0;
            if (pct >= 0) {
              printf "✔ %s: %s -> %s (体积缩小 %.1f%%)\n", name, hr(o), hr(n), pct;
            } else {
              printf "✔ %s: %s -> %s (体积增大 +%.1f%%)\n", name, hr(o), hr(n), -pct;
            }
          }
        '
      else
        echo "✖ 转换失败: $file"
      fi
    else
      echo "警告: 文件不存在 -> $file"
    fi
  done
}
