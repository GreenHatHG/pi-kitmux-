"""Tests for the tmux after-select-* ack helper."""

import importlib.util
from pathlib import Path
from unittest import TestCase, mock

_SCRIPT = Path(__file__).resolve().parent.parent / "tmux-pi-ack.py"
_spec = importlib.util.spec_from_file_location("tmux_pi_ack", _SCRIPT)
assert _spec is not None and _spec.loader is not None
pi_ack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pi_ack)

SEP = "\x1f"


def _pane(window: str, pane: str, running: bool = False, done: bool = False):
    return pi_ack.Pane(window, pane, running, done)


class WindowStatusTest(TestCase):
    def test_running_wins_over_done(self) -> None:
        panes = [_pane("@1", "%1", done=True), _pane("@1", "%2", running=True)]
        self.assertEqual(pi_ack.window_status(panes), "⏳ ")

    def test_done_when_nothing_running(self) -> None:
        self.assertEqual(pi_ack.window_status([_pane("@1", "%1", done=True)]), "✅ ")

    def test_idle_is_empty(self) -> None:
        self.assertEqual(pi_ack.window_status([_pane("@1", "%1")]), "")


class SessionTotalTest(TestCase):
    def test_both_counts(self) -> None:
        panes = [
            _pane("@1", "%1", running=True),
            _pane("@1", "%2", running=True),
            _pane("@2", "%3", done=True),
        ]
        self.assertEqual(pi_ack.session_total(panes), "⏳2 ✅1 ")

    def test_running_only(self) -> None:
        self.assertEqual(
            pi_ack.session_total([_pane("@1", "%1", running=True)]), "⏳1 "
        )

    def test_done_only(self) -> None:
        self.assertEqual(pi_ack.session_total([_pane("@1", "%1", done=True)]), "✅1 ")

    def test_none_is_empty(self) -> None:
        self.assertEqual(pi_ack.session_total([_pane("@1", "%1")]), "")


class ReadPanesTest(TestCase):
    def test_parses_flags_and_skips_blank(self) -> None:
        output = f"@1{SEP}%1{SEP}1{SEP}\n@1{SEP}%2{SEP}{SEP}1\n\nbroken{SEP}line\n"
        with mock.patch.object(pi_ack, "tmux", return_value=output):
            panes = pi_ack.read_panes("$0")
        self.assertEqual(
            panes,
            [_pane("@1", "%1", running=True), _pane("@1", "%2", done=True)],
        )


class AckTest(TestCase):
    def _run(self, window: str, session: str, list_output: str) -> list[list[str]]:
        calls: list[list[str]] = []

        def fake_tmux(args: list[str]) -> str:
            calls.append(list(args))
            if args[0] == "display-message":
                return session
            if args[0] == "list-panes":
                return list_output
            return ""

        with mock.patch.object(pi_ack, "tmux", side_effect=fake_tmux):
            pi_ack.ack(window)
        return calls

    def test_clears_done_only_in_target_window(self) -> None:
        list_output = (
            f"@1{SEP}%1{SEP}{SEP}1\n@1{SEP}%2{SEP}1{SEP}\n@2{SEP}%3{SEP}{SEP}1\n"
        )
        calls = self._run("@1", "$0", list_output)
        unset = [c for c in calls if c[:2] == ["set", "-pu"]]
        self.assertEqual(unset, [["set", "-pu", "-t", "%1", "@pi_done"]])

    def test_rewrites_window_status_and_session_total(self) -> None:
        list_output = f"@1{SEP}%1{SEP}{SEP}1\n@2{SEP}%3{SEP}{SEP}1\n"
        calls = self._run("@1", "$0", list_output)
        # After acking @1: @1 is empty, @2 is still ✅
        self.assertIn(["set", "-wq", "-t", "@1", "@pi_win", ""], calls)
        self.assertIn(["set", "-wq", "-t", "@2", "@pi_win", "✅ "], calls)
        self.assertIn(["set", "-q", "-t", "$0", "@pi_total", "✅1 "], calls)
        self.assertIn(["refresh-client", "-S"], calls)

    def test_running_window_keeps_running_glyph_after_ack(self) -> None:
        list_output = f"@1{SEP}%1{SEP}1{SEP}1\n"
        calls = self._run("@1", "$0", list_output)
        self.assertIn(["set", "-wq", "-t", "@1", "@pi_win", "⏳ "], calls)
        self.assertIn(["set", "-q", "-t", "$0", "@pi_total", "⏳1 "], calls)

    def test_missing_session_is_a_noop(self) -> None:
        with mock.patch.object(pi_ack, "tmux", return_value="") as tmux_mock:
            pi_ack.ack("@1")
        tmux_mock.assert_called_once()


class MainTest(TestCase):
    def test_calls_ack_with_window_id(self) -> None:
        with mock.patch.object(pi_ack, "ack") as ack_mock:
            self.assertEqual(pi_ack.main(["tmux-pi-ack.py", "@3"]), 0)
        ack_mock.assert_called_once_with("@3")

    def test_no_argument_is_a_noop(self) -> None:
        with mock.patch.object(pi_ack, "ack") as ack_mock:
            self.assertEqual(pi_ack.main(["tmux-pi-ack.py"]), 0)
        ack_mock.assert_not_called()
