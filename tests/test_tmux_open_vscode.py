"""Tests for tmux-open-vscode.py (the VPS-side `bind v` helper)."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path(__file__).resolve().parent.parent / "tmux-open-vscode.py"
_spec = importlib.util.spec_from_file_location("tmux_open_vscode", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # register so mock.patch can target the module
_spec.loader.exec_module(mod)


class MainTest(unittest.TestCase):
    def run_main(self, argv: list[str]) -> int:
        return mod.main(argv)

    def test_no_args_is_usage_error(self) -> None:
        with mock.patch("sys.stderr") as err:
            self.assertEqual(self.run_main(["x"]), 1)
        err.write.assert_called()  # usage printed

    def test_relative_path_is_rejected(self) -> None:
        with mock.patch("sys.stderr") as err:
            self.assertEqual(self.run_main(["x", "relative/path"]), 1)
        err.write.assert_called()

    @mock.patch("tmux_open_vscode.via_clipboard")
    @mock.patch("tmux_open_vscode.via_tunnel", return_value=None)
    def test_tunnel_success_skips_fallback(self, tunnel: mock.MagicMock, clip: mock.MagicMock) -> None:
        self.assertEqual(self.run_main(["x", "/home/me/proj"]), 0)
        tunnel.assert_called_once_with("/home/me/proj")
        clip.assert_not_called()

    @mock.patch("tmux_open_vscode.via_clipboard")
    @mock.patch("tmux_open_vscode.via_tunnel", return_value="failed")
    def test_tunnel_failure_falls_back(self, tunnel: mock.MagicMock, clip: mock.MagicMock) -> None:
        self.assertEqual(self.run_main(["x", "/home/me/proj"]), 0)
        clip.assert_called_once_with("/home/me/proj")


class TunnelTest(unittest.TestCase):
    @mock.patch("socket.create_connection")
    def test_ok_reply_means_tunnel_handled_it(self, conn: mock.MagicMock) -> None:
        sock = mock.MagicMock()
        sock.recv.return_value = b"OK"
        conn.return_value.__enter__.return_value = sock
        self.assertIsNone(mod.via_tunnel("/home/me/proj"))

    @mock.patch("socket.create_connection")
    def test_listener_reply_is_surfaced(self, conn: mock.MagicMock) -> None:
        sock = mock.MagicMock()
        sock.recv.return_value = b"rejected: not under prefix"
        conn.return_value.__enter__.return_value = sock
        with mock.patch("sys.stderr") as err:
            self.assertEqual(mod.via_tunnel("/home/me/proj"), "failed")
        err.write.assert_called()

    @mock.patch("socket.create_connection", side_effect=ConnectionRefusedError("refused"))
    def test_refused_mentions_tunnel(self, _conn: mock.MagicMock) -> None:
        with mock.patch("sys.stderr") as err:
            self.assertEqual(mod.via_tunnel("/home/me/proj"), "failed")
        err.write.assert_called()


class ClipboardTest(unittest.TestCase):
    @mock.patch("sys.stderr")
    @mock.patch("sys.stdout")
    def test_writes_osc52_passthrough(self, out: mock.MagicMock, err: mock.MagicMock) -> None:
        mod.via_clipboard("/home/me/proj")
        seq = out.write.call_args[0][0]
        self.assertTrue(seq.startswith("\033Ptmux;"))  # DCS passthrough wrap
        self.assertIn("\033]52;c;", seq)               # OSC 52 clipboard write
        self.assertTrue(seq.endswith("\a\033\\"))
        stderr = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("URL copied to Mac clipboard", stderr)


class UrlPrefixTest(unittest.TestCase):
    def test_prefix_matches_helper_and_listener(self) -> None:
        # The VPS helper and the Mac listener must agree on the whitelist
        self.assertEqual(mod.URL_PREFIX, "vscode://vscode-remote/ssh-remote+vps/")


if __name__ == "__main__":
    unittest.main()
