#!/usr/bin/env python3
"""Print the invisible cross-machine tag for the tmux terminal title.

tmux.conf's set-titles-string appends `#(tmux-title-tag.py '#S')`: the tag
("host:session") is encoded as zero-width characters, so the Mac kitty tab
shows a clean "⏳3 repo" while switch-pi-agent.py's picker still decodes the
tag from kitty ls's raw title to map a tmux session to its kitty tab
(pids cannot be compared across machines, the title is the only channel).
"""
import os
import sys

# The zero-width alphabet: bit 0 / bit 1 / payload start / payload end.
# All four render as nothing in kitty (zero width, no glyph).
Z0 = "\u200b"  # zero width space
Z1 = "\u200c"  # zero width non-joiner
START = "\u2060"  # word joiner
END = "\ufeff"  # BOM


def encode_tag(payload: str) -> str:
    """Encode text as invisible chars: START + 8 bits per byte (Z0/Z1) + END."""
    bits = "".join(format(b, "08b") for b in payload.encode())
    return START + "".join(Z0 if b == "0" else Z1 for b in bits) + END


if __name__ == "__main__":
    session = sys.argv[1] if len(sys.argv) > 1 else ""
    if session:
        host = os.uname().nodename.split(".")[0]
        print(encode_tag(f"{host}:{session}"), end="")
