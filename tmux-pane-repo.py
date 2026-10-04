#!/usr/bin/env python3
import os
import re
import subprocess
import sys
from collections.abc import Callable

GIT_TIMEOUT = 2
GIT = "/usr/bin/git"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

GitRunner = Callable[[str], str]


def run_git(cwd: str) -> str:
    """Return ``<git-dir>\n<common-dir>``; "" if this is not a git folder."""
    try:
        # Ask for full paths, or we can't tell a worktree from the main repo.
        return subprocess.check_output(
            [
                GIT,
                "-C",
                cwd,
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
                "--git-common-dir",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        # Any git failure just means "not a repo", so keep it quiet.
        return ""


def parse_paths(output: str) -> tuple[str, str] | None:
    """Take (git-dir, common-dir) from ``rev-parse`` output; None if wrong count."""
    lines = [line for line in output.splitlines() if line]
    # We want exactly two paths, anything else is junk.
    if len(lines) != 2:
        return None
    return lines[0], lines[1]


def repo_name(common_dir: str) -> str:
    """Project name. When common-dir is ``<repo>/.git`` use the parent folder."""
    return _safe_component(
        os.path.basename(os.path.dirname(common_dir))
        if os.path.basename(common_dir) == ".git"
        else os.path.basename(common_dir)
    )


def _safe_component(value: str) -> str:
    """Drop control chars so they can't wreck the tmux status line."""
    return _CONTROL_CHARS.sub("", value)


def resolve_location(cwd: str, git_runner: GitRunner = run_git) -> str:
    """Return ``<repo>/<place>`` for a git checkout, otherwise an empty string.

    A missing or non-directory cwd returns early, before spawning git.
    """
    if not cwd or not os.path.isdir(cwd):
        return ""
    paths = parse_paths(git_runner(cwd))
    if paths is None:
        return ""
    git_dir, common_dir = paths
    # Same path means the main checkout; otherwise it's a linked worktree.
    where = "main" if git_dir == common_dir else f"wt:{os.path.basename(git_dir)}"
    name = repo_name(common_dir)
    if not name:
        return ""
    # Clean the place too, it comes from a folder name.
    return f"{name}/{_safe_component(where)}"


def main(argv: list[str]) -> int:
    # No path given? Just pass "" and let resolve_location say no.
    print(resolve_location(argv[1] if len(argv) > 1 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))