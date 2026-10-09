"""Tests for extracting the real application behind ``enclave run``."""

import importlib.util
from pathlib import Path
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "tmux-pane-command.py"
_spec = importlib.util.spec_from_file_location("tmux_pane_command", _SCRIPT)
assert _spec is not None and _spec.loader is not None
pane_command = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pane_command)


class ParseEnclaveCommandTest(TestCase):
    def test_extracts_plain_command(self) -> None:
        self.assertEqual(pane_command.parse_enclave_command("enclave run pi"), "pi")

    def test_extracts_command_after_separator(self) -> None:
        self.assertEqual(
            pane_command.parse_enclave_command(
                "enclave run -- claude --dangerously-skip-permissions"
            ),
            "claude",
        )

    def test_skips_long_options(self) -> None:
        self.assertEqual(
            pane_command.parse_enclave_command(
                "enclave run --config custom.toml --allow-write /tmp -- npm run dev"
            ),
            "npm",
        )

    def test_skips_short_and_repeated_options(self) -> None:
        self.assertEqual(
            pane_command.parse_enclave_command(
                "enclave run -c one.toml -w /tmp -w /var/tmp /opt/homebrew/bin/pi"
            ),
            "pi",
        )

    def test_skips_equals_options(self) -> None:
        self.assertEqual(
            pane_command.parse_enclave_command(
                "enclave run --config=one.toml --allow-write=/tmp -- pi"
            ),
            "pi",
        )

    def test_rejects_non_enclave_and_incomplete_commands(self) -> None:
        self.assertIsNone(pane_command.parse_enclave_command("pi"))
        self.assertIsNone(pane_command.parse_enclave_command("enclave run --"))
        self.assertIsNone(
            pane_command.parse_enclave_command("enclave run --future x pi")
        )
        self.assertIsNone(
            pane_command.parse_enclave_command("enclave run 'unterminated")
        )


class ResolvePaneCommandTest(TestCase):
    def test_reads_foreground_leader_command(self) -> None:
        calls: list[list[str]] = []

        def fake_ps(args: list[str]) -> str:
            calls.append(args)
            if args[-1] == "tpgid=":
                return " 12640\n"
            return "enclave run pi\n"

        self.assertEqual(
            pane_command.resolve_pane_command(12519, ps_runner=fake_ps), "pi"
        )
        self.assertEqual(
            calls,
            [
                ["-p", "12519", "-o", "tpgid="],
                ["-p", "12640", "-o", "command="],
            ],
        )

    def test_falls_back_when_process_changes(self) -> None:
        self.assertEqual(
            pane_command.resolve_pane_command(123, "enclave", lambda _args: ""),
            "enclave",
        )

    def test_shows_real_command_when_leader_is_not_a_wrapper(self) -> None:
        # Helper is only called for enclave/interpreter panes; if the leader is
        # something else (e.g. a race), showing its real name beats the fallback
        responses = iter(["456", "vim README.md"])
        self.assertEqual(
            pane_command.resolve_pane_command(
                123, "enclave", lambda _args: next(responses)
            ),
            "vim",
        )

    def test_unwraps_direct_pi_with_rewritten_title(self) -> None:
        # pi sets process.title = "pi", so ps command shows just "pi" even
        # though the kernel comm says "node"; the first token is the real app
        responses = iter([" 12640\n", "pi     \n"])
        self.assertEqual(
            pane_command.resolve_pane_command(
                123, "node", lambda _args: next(responses)
            ),
            "pi",
        )

    def test_falls_back_for_bare_interpreter(self) -> None:
        # A bare `node server.js` has no better name than the kernel one
        self.assertEqual(
            pane_command.resolve_pane_command(
                123, "node", lambda _args: " 12640\nnode server.js\n"
            ),
            "node",
        )

    def test_falls_back_for_non_interpreter_command(self) -> None:
        self.assertEqual(
            pane_command.resolve_pane_command(
                123, "node", lambda _args: " 12640\nvim README.md\n"
            ),
            "node",
        )

    def test_sanitizes_fallback(self) -> None:
        self.assertEqual(
            pane_command.resolve_pane_command(123, "bad\x07name", lambda _args: ""),
            "badname",
        )
