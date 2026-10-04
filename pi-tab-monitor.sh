#!/usr/bin/env bash

# Process pattern that matches your Pi Agent (tweak to your real command, e.g. 'pi' or 'pi-coding-agent')
PROCESS_PATTERN="pi"

update_kitty_title() {
    local title="$1"
    # Send the title straight to Kitty with a tmux escape sequence
    if [ -n "$TMUX" ]; then
        printf "\033Ptmux;\033\033]2;%s\007\033\\" "$title"
    else
        printf "\033]2;%s\007" "$title"
    fi
}

last_state=""

while true; do
    # Count matching agent processes (skip this grep/pgrep itself)
    count=$(pgrep -f "$PROCESS_PATTERN" | grep -v "$$" | wc -l | tr -d ' ')

    if [ "$count" -gt 0 ]; then
        current_state="running"
        title="[⏳ running ($count)] Pi Agent"
    else
        current_state="idle"
        title="[✅ done] Pi Agent"
    fi

    # Only update the tab title when state or count changes, to avoid constant redraws
    if [ "$title" != "$last_state" ]; then
        update_kitty_title "$title"
        last_state="$title"
    fi

    sleep 2
done
