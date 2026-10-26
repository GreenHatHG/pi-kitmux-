"""Tests for tmux-open-vscode.py (the VPS-side `bind v` helper)."""

import importlib.util
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path(__file__).resolve().parent.parent / "tmux-open-vscode.py"
_spec = importlib.util.spec_from_file_location("tmux_open_vscode", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # register so mock.patch can target the module
_spec.loader.exec_module(mod)

# A prefix under a dir that does not exist, so a test never touches the real
# per-connection sockets of the machine it runs on.
_NO_SOCKETS = "/nonexistent/vscode-open-remote"


class NoSocketMixin(unittest.TestCase):
    """Force discovery to find no socket file, leaving only the fixed-port fallback."""

    def setUp(self) -> None:
        patcher = mock.patch.object(mod, "SOCK_PREFIX", _NO_SOCKETS)
        patcher.start()
        self.addCleanup(patcher.stop)


class MainTest(NoSocketMixin):
    def test_no_args_is_usage_error(self) -> None:
        with mock.patch("sys.stderr") as err:
            self.assertEqual(mod.main(["x"]), 1)
        err.write.assert_called()  # usage printed

    def test_relative_path_is_rejected(self) -> None:
        with mock.patch("sys.stderr") as err:
            self.assertEqual(mod.main(["x", "relative/path"]), 1)
        err.write.assert_called()

    @mock.patch("tmux_open_vscode.via_clipboard")
    @mock.patch("tmux_open_vscode.via_tunnel", return_value=None)
    def test_tunnel_success_skips_fallback(
        self, tunnel: mock.MagicMock, clip: mock.MagicMock
    ) -> None:
        self.assertEqual(mod.main(["x", "/home/me/proj"]), 0)
        tunnel.assert_called_once_with("/home/me/proj")
        clip.assert_not_called()

    @mock.patch("tmux_open_vscode.via_clipboard")
    @mock.patch("tmux_open_vscode.via_tunnel", return_value="failed")
    def test_tunnel_failure_falls_back(
        self, _tunnel: mock.MagicMock, clip: mock.MagicMock
    ) -> None:
        self.assertEqual(mod.main(["x", "/home/me/proj"]), 0)
        clip.assert_called_once_with("/home/me/proj")

    def test_probe_needs_no_path(self) -> None:
        with mock.patch("tmux_open_vscode.probe", return_value=0) as probe:
            self.assertEqual(mod.main(["x", "--probe"]), 0)
        probe.assert_called_once_with()


class LegacyTunnelTest(NoSocketMixin):
    """The fixed-port fallback, which is all that is left when no socket file exists."""

    @mock.patch("socket.create_connection")
    def test_ok_reply_means_tunnel_handled_it(self, conn: mock.MagicMock) -> None:
        conn.return_value.recv.return_value = b"OK"
        self.assertIsNone(mod.via_tunnel("/home/me/proj"))
        # the socket must be closed again, or tmux leaks one fd per keypress
        conn.return_value.close.assert_called_once()

    @mock.patch("socket.create_connection")
    def test_listener_reply_is_surfaced(self, conn: mock.MagicMock) -> None:
        conn.return_value.recv.return_value = b"rejected: not under prefix"
        with mock.patch("sys.stderr") as err:
            self.assertEqual(mod.via_tunnel("/home/me/proj"), "failed")
        err.write.assert_called()

    @mock.patch(
        "socket.create_connection", side_effect=ConnectionRefusedError("refused")
    )
    def test_refused_explains_itself(self, _conn: mock.MagicMock) -> None:
        with mock.patch("sys.stderr") as err:
            self.assertEqual(mod.via_tunnel("/home/me/proj"), "failed")
        text = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("no usable vscode tunnel", text)


class SocketDiscoveryTest(unittest.TestCase):
    """Each connection owns one socket; leftovers must not be handed a URL."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.prefix = tmp.name + "/vscode-open-remote"
        patcher = mock.patch.object(mod, "SOCK_PREFIX", self.prefix)
        patcher.start()
        self.addCleanup(patcher.stop)

    def listening(self, name: str) -> str:
        path = f"{self.prefix}-{name}"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        self.addCleanup(srv.close)
        return path

    def unix_paths(self) -> list:
        return [target for kind, target in self.targets() if kind == "unix"]

    def targets(self) -> list:
        # pylint: disable=protected-access  # the discovery helper is what we are testing
        return mod._targets()

    def test_socket_file_is_a_candidate(self) -> None:
        self.assertIn(self.listening("1-1"), self.unix_paths())

    def test_dead_socket_file_is_still_a_candidate(self) -> None:
        dead = f"{self.prefix}-2-2"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(dead)  # creates the file
        srv.close()  # listener gone, file stays behind
        self.assertTrue(Path(dead).exists())
        # No pre-filter on purpose: refusing it costs one instant connect, while a
        # pre-probe would open a second tunnel connection for every candidate
        self.assertIn(dead, self.unix_paths())

    def test_a_plain_file_is_not_a_candidate(self) -> None:
        Path(f"{self.prefix}-3-3").touch()
        self.assertEqual(self.unix_paths(), [])

    def test_both_legacy_stacks_are_tried(self) -> None:
        self.listening("4-4")
        # Literal on purpose: comparing to mod.LEGACY_ADDRS would still pass if the
        # list collapsed back to one entry, which is the bug this covers
        self.assertEqual(
            [target for kind, target in self.targets() if kind == "tcp"],
            [("127.0.0.1", 19998), ("::1", 19998)],
        )


class TunnelOrderTest(unittest.TestCase):
    """Which tunnel wins, and when the hunt stops early."""

    def run_with(self, replies: list) -> tuple:
        targets = [
            ("unix", "/tmp/fake-a"),
            ("unix", "/tmp/fake-b"),
            ("tcp", mod.LEGACY_ADDRS[0]),
        ]
        with (
            mock.patch.object(mod, "_targets", return_value=targets),
            mock.patch.object(mod, "_attempt", side_effect=replies) as attempt,
            mock.patch("sys.stderr"),
        ):
            result = mod.via_tunnel("/home/me/proj")
        return result, attempt

    def test_second_tunnel_wins_when_first_has_a_dead_peer(self) -> None:
        # A roam leaves the listener alive with its stream gone: connect ok, no reply
        result, attempt = self.run_with([(None, "TimeoutError: silent"), ("OK", None)])
        self.assertIsNone(result)
        self.assertEqual(attempt.call_count, 2)

    def test_rejection_stops_the_hunt(self) -> None:
        result, attempt = self.run_with([("rejected: nope", None), ("OK", None)])
        self.assertEqual(result, "failed")
        # every tunnel ends at the same Mac listener, so the answer would not change
        self.assertEqual(attempt.call_count, 1)

    def test_odd_reply_moves_on(self) -> None:
        result, attempt = self.run_with([("empty request", None), ("OK", None)])
        self.assertIsNone(result)
        self.assertEqual(attempt.call_count, 2)

    def test_all_tunnels_failing_is_a_failure(self) -> None:
        result, attempt = self.run_with(
            [
                (None, "ConnectionRefusedError: no"),
                (None, "TimeoutError: silent"),
                (None, "x"),
            ]
        )
        self.assertEqual(result, "failed")
        self.assertEqual(attempt.call_count, 3)


class ProbeTest(unittest.TestCase):
    """--probe proves the chain without ever opening a window."""

    def test_rejection_proves_the_chain(self) -> None:
        with (
            mock.patch.object(
                mod, "_targets", return_value=[("tcp", mod.LEGACY_ADDRS[0])]
            ),
            mock.patch.object(
                mod, "_attempt", return_value=("rejected: not under x", None)
            ),
            mock.patch("sys.stdout"),
        ):
            self.assertEqual(mod.probe(), 0)

    def test_dead_chain_fails(self) -> None:
        with (
            mock.patch.object(
                mod, "_targets", return_value=[("tcp", mod.LEGACY_ADDRS[0])]
            ),
            mock.patch.object(
                mod, "_attempt", return_value=(None, "ConnectionRefusedError: no")
            ),
            mock.patch("sys.stderr"),
        ):
            self.assertEqual(mod.probe(), 2)


class ClipboardTest(unittest.TestCase):
    @mock.patch("sys.stderr")
    @mock.patch("sys.stdout")
    def test_writes_osc52_passthrough(
        self, out: mock.MagicMock, err: mock.MagicMock
    ) -> None:
        mod.via_clipboard("/home/me/proj")
        seq = out.write.call_args[0][0]
        self.assertTrue(seq.startswith("\033Ptmux;"))  # DCS passthrough wrap
        self.assertIn("\033]52;c;", seq)  # OSC 52 clipboard write
        self.assertTrue(seq.endswith("\a\033\\"))
        stderr = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("URL copied to Mac clipboard", stderr)


class UrlPrefixTest(unittest.TestCase):
    def test_prefix_matches_helper_and_listener(self) -> None:
        # The VPS helper and the Mac listener must agree on the whitelist
        self.assertEqual(mod.URL_PREFIX, "vscode://vscode-remote/ssh-remote+vps/")


if __name__ == "__main__":
    unittest.main()
