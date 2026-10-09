#!/usr/bin/env python3
"""Find the real app behind ``enclave run`` or an interpreter shebang."""

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
# Interpreters whose kernel name (p_comm) hides the real app: a script like
# pi is a node shebang, so p_comm says "node" and process.title can't fix it
# (it only rewrites the argv area, which ps comm/command DO show).
_INTERPRETERS = {"node", "bun", "deno", "python", "python3"}

PsRunner = Callable[[list[str]], str]


def _safe_basename(value: str) -> str | None:
    """Return an executable name that is safe for the status line."""
    name = os.path.basename(value.strip())
    name = _CONTROL_CHARS.sub("", name)
    return name or None


def parse_enclave_command(command_line: str) -> str | None:
    """Pull ``<command>`` out of ``enclave run [options] -- <command>``."""
    try:
        tokens = shlex.split(command_line)
    except ValueError:
        return None

    if (
        len(tokens) < 3
        or os.path.basename(tokens[0]) != "enclave"
        or tokens[1] != "run"
    ):
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
    """Run macOS ps with a short timeout; return "" on error or race."""
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
    """Read the pane leader's command and unwrap enclave or an interpreter."""
    safe_fallback = _safe_basename(fallback) or "enclave"
    raw_tpgid = ps_runner(["-p", str(pane_pid), "-o", "tpgid="]).strip()
    try:
        tpgid = int(raw_tpgid)
    except ValueError:
        return safe_fallback
    if tpgid <= 0:
        return safe_fallback

    command_line = ps_runner(["-p", str(tpgid), "-o", "command="]).strip()
    unwrapped = parse_enclave_command(command_line)
    if unwrapped:
        return unwrapped

    # The helper is only called when pane_current_command is enclave or an
    # interpreter. ps command shows the rewritten title (pi sets it to "pi",
    # wiping the rest of argv), so the first token is the real app name.
    try:
        tokens = shlex.split(command_line)
    except ValueError:
        return safe_fallback
    if tokens:
        name = _safe_basename(tokens[0])
        # A bare interpreter (no title rewrite, like `node server.js`) has no
        # better name than the kernel one, so fall back instead of lying.
        if name and name not in _INTERPRETERS:
            return name
    return safe_fallback


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
