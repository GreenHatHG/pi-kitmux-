"""Regression tests for the title-resend contract in tmux.conf.

kitty-tab-sync.ts forces a second title push by flipping @pi_nonce, and that only
works because set-titles-string renders it. Without the suffix the whole resend
becomes a silent no-op — nothing errors, the tab just stays stale after a roam —
so the line is pinned here rather than left to a comment.
"""

from pathlib import Path
from unittest import TestCase

_CONF = Path(__file__).resolve().parent.parent / "tmux.conf"


def _set_titles_string() -> str:
    for line in _CONF.read_text(encoding="utf-8").splitlines():
        if line.startswith("set -g set-titles-string"):
            return line
    raise AssertionError("set-titles-string missing from tmux.conf")


def _after_nonce(line: str) -> str:
    """Everything the line still renders after @pi_nonce, minus trailing whitespace."""
    return line.split("#{@pi_nonce}", 1)[1].strip()


class SetTitlesStringTest(TestCase):
    def test_renders_the_resend_nonce(self) -> None:
        self.assertIn("#{@pi_nonce}", _set_titles_string())

    def test_nonce_comes_after_the_zero_width_tag(self) -> None:
        # The tag's #() output ends with the frame's end marker; the nonce has to sit
        # behind it so it never lands inside the zero-width run the picker decodes.
        line = _set_titles_string()
        self.assertIn("#{@pi_nonce}", line)
        self.assertLess(line.index("tmux-title-tag.py"), line.index("#{@pi_nonce}"))

    def test_nonce_is_the_last_thing_rendered(self) -> None:
        # The flip must change the string tmux compares. Anything rendered after the
        # nonce would still differ per flip, but keeping it last is what makes the diff
        # exactly one trailing char (verified against real OSC payloads).
        # Only a closing quote may follow: single and double quotes both work in tmux
        # and produce the same rendered title, so accepting just one is a false alarm
        # waiting for a cosmetic edit.
        line = _set_titles_string()
        self.assertIn("#{@pi_nonce}", line)
        self.assertIn(_after_nonce(line), ("'", '"'), f"unexpected suffix: {line!r}")
