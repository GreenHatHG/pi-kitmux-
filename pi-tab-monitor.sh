#!/usr/bin/env bash

# 匹配你运行 Pi Agent 的进程特征（根据你实际启动命令调整，如 'pi' 或 'pi-coding-agent'）
PROCESS_PATTERN="pi"

update_kitty_title() {
    local title="$1"
    # 通过 Tmux 转义序列将标题直接打给 Kitty
    if [ -n "$TMUX" ]; then
        printf "\033Ptmux;\033\033]2;%s\007\033\\" "$title"
    else
        printf "\033]2;%s\007" "$title"
    fi
}

last_state=""

while true; do
    # 统计匹配到的 Agent 进程数量（排除当前 grep/pgrep 自身）
    count=$(pgrep -f "$PROCESS_PATTERN" | grep -v "$$" | wc -l | tr -d ' ')

    if [ "$count" -gt 0 ]; then
        current_state="running"
        title="[⏳ 运行中 ($count)] Pi Agent"
    else
        current_state="idle"
        title="[✅ 已完成] Pi Agent"
    fi

    # 仅在状态或数量变化时更新 Tab 标题，避免频繁刷新
    if [ "$title" != "$last_state" ]; then
        update_kitty_title "$title"
        last_state="$title"
    fi

    sleep 2
done
