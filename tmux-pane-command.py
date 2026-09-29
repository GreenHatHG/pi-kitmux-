#!/usr/bin/env python3
"""Resolve the application wrapped by ``enclave run`` for a tmux pane."""

import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable

PS_TIMEOUT = 1
_VALUE_OPTIONS = {"--config", "-c", "--allow-write", "-w"}
_VALUE_OPTION_PREFIXES = ("--config=", "--allow-write=")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

PsRunner = Callable[[list[str]], str]


def _safe_basename(value: str) -> str | None:
    """Return a status-line-safe executable basename."""
    name = os.path.basename(value.strip())
    name = _CONTROL_CHARS.sub("", name)
    return name or None


def parse_enclave_command(command_line: str) -> str | None:
    """Extract ``<command>`` from an ``enclave run [options] -- <command>`` line."""
    try:
        tokens = shlex.split(command_line)
    except ValueError:
        return None

    if len(tokens) < 3 or os.path.basename(tokens[0]) != "enclave" or tokens[1] != "run":
        return None

    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in _VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith(_VALUE_OPTION_PREFIXES):
            index += 1
            continue
        if token.startswith("-"):
            return None
        break

    if index >= len(tokens):
        return None
    return _safe_basename(tokens[index])


def run_ps(args: list[str]) -> str:
    """Run macOS ps with a short timeout; return an empty string on races/errors."""
    try:
        return subprocess.check_output(
            ["/bin/ps", *args],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=PS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve_pane_command(
    pane_pid: int, fallback: str = "enclave", ps_runner: PsRunner = run_ps
) -> str:
    """Read the pane foreground leader's launch command and unwrap enclave."""
    safe_fallback = _safe_basename(fallback) or "enclave"
    raw_tpgid = ps_runner(["-p", str(pane_pid), "-o", "tpgid="]).strip()
    try:
        tpgid = int(raw_tpgid)
    except ValueError:
        return safe_fallback
    if tpgid <= 0:
        return safe_fallback

    command_line = ps_runner(["-p", str(tpgid), "-o", "command="]).strip()
    return parse_enclave_command(command_line) or safe_fallback


def main(argv: list[str]) -> int:
    fallback = argv[2] if len(argv) > 2 else "enclave"
    try:
        pane_pid = int(argv[1])
    except (IndexError, ValueError):
        print(_safe_basename(fallback) or "enclave")
        return 0

    print(resolve_pane_command(pane_pid, fallback))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
