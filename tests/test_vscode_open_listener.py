"""Tests for scripts/vscode-open-listener.py (the Mac-side opener)."""

import importlib.util
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vscode-open-listener.py"
_spec = importlib.util.spec_from_file_location("vscode_open_listener", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # register so mock.patch can target the module
_spec.loader.exec_module(mod)

_URL = "vscode://vscode-remote/ssh-remote+vps/home/me/proj"


class FakeConn:
    """Just enough of a socket for handle() to talk to."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.sent: list[bytes] = []

    def recv(self, _n: int) -> bytes:
        return self.payload

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def settimeout(self, _t: float) -> None:
        pass

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *_a: object) -> None:
        pass


class HandleTest(unittest.TestCase):
    def run_handle(self, payload: bytes) -> tuple[FakeConn, list]:
        conn = FakeConn(payload)
        opened: list = []
        with mock.patch.object(
            mod.subprocess, "run", side_effect=lambda args, **_k: opened.append(args)
        ):
            mod.handle(conn)
        return conn, opened

    def test_whitelisted_url_is_opened(self) -> None:
        conn, opened = self.run_handle(_URL.encode())
        self.assertEqual(conn.sent, [b"OK"])
        self.assertEqual(opened, [["open", _URL]])

    def test_foreign_url_is_rejected_and_never_opened(self) -> None:
        conn, opened = self.run_handle(b"http://evil")
        self.assertTrue(conn.sent[0].startswith(b"rejected:"))
        self.assertEqual(opened, [])

    def test_nul_byte_is_rejected(self) -> None:
        conn, opened = self.run_handle(_URL.encode() + b"\0x")
        self.assertTrue(conn.sent[0].startswith(b"rejected:"))
        self.assertEqual(opened, [])

    def test_empty_request_gets_an_answer(self) -> None:
        conn, opened = self.run_handle(b"")
        self.assertEqual(conn.sent, [b"empty request"])
        self.assertEqual(opened, [])

    def test_ack_comes_before_open(self) -> None:
        # The VPS helper retries on silence, so a slow open must not delay the OK
        gate = threading.Event()
        self.addCleanup(gate.set)
        conn = FakeConn(_URL.encode())
        with mock.patch.object(mod.subprocess, "run", side_effect=lambda *a, **k: gate.wait(10)):
            worker = threading.Thread(target=mod.handle, args=(conn,))
            worker.start()
            deadline = time.monotonic() + 2
            while not conn.sent and time.monotonic() < deadline:
                time.sleep(0.01)
            acked = list(conn.sent)  # read while `open` is still blocked
            gate.set()
            worker.join(5)
        self.assertEqual(acked, [b"OK"])
        self.assertFalse(worker.is_alive())

    def test_a_crashing_open_does_not_kill_the_thread(self) -> None:
        conn = FakeConn(_URL.encode())
        with mock.patch.object(mod.subprocess, "run", side_effect=ValueError("nope")):
            mod.handle(conn)  # must not raise
        self.assertEqual(conn.sent, [b"OK"])


class ServeTest(unittest.TestCase):
    def test_a_slow_open_does_not_block_the_next_connection(self) -> None:
        # One thread per request. Single-threaded, the second caller gets no ack until
        # the first `open` returns — the VPS helper reads that as a dead tunnel and
        # sends the same URL again, which pops a second window.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]

        gate = threading.Event()
        self.addCleanup(gate.set)
        with mock.patch.object(mod.subprocess, "run", side_effect=lambda *a, **k: gate.wait(10)):
            threading.Thread(target=mod.serve, args=(srv,), daemon=True).start()
            first = self.send(port, _URL + "/a")  # this one blocks inside open
            second = self.send(port, _URL + "/b")
        self.assertEqual(first, b"OK")
        self.assertEqual(second, b"OK")

    @staticmethod
    def send(port: int, url: str) -> bytes:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(url.encode())
            return sock.recv(4096)


class PrefixTest(unittest.TestCase):
    def test_prefix_matches_helper_and_listener(self) -> None:
        self.assertEqual(mod.URL_PREFIX, "vscode://vscode-remote/ssh-remote+vps/")


if __name__ == "__main__":
    unittest.main()
