"""Regression tests for tmux-pane-repo's pure functions and parsing."""

import importlib.util
from pathlib import Path
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "tmux-pane-repo.py"
_spec = importlib.util.spec_from_file_location("tmux_pane_repo", _SCRIPT)
assert _spec is not None and _spec.loader is not None  # path always exists; just narrows types
pane_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pane_repo)


class ParsePathsTest(TestCase):
    def test_returns_git_dir_and_common_dir(self) -> None:
        self.assertEqual(
            pane_repo.parse_paths("/r/.git/worktrees/5\n/r/.git\n"),
            ("/r/.git/worktrees/5", "/r/.git"),
        )

    def test_rejects_missing_or_extra_lines(self) -> None:
        # In a non-git dir git errors and stdout is empty
        self.assertIsNone(pane_repo.parse_paths(""))
        self.assertIsNone(pane_repo.parse_paths("/r/.git\n"))
        self.assertIsNone(pane_repo.parse_paths("/a\n/b\n/c\n"))


class RepoNameTest(TestCase):
    def test_standard_git_dir_uses_parent(self) -> None:
        self.assertEqual(
            pane_repo.repo_name("/Users/x/Projects/pi-kitmux/.git"), "pi-kitmux"
        )

    def test_bare_repo_uses_its_own_name(self) -> None:
        self.assertEqual(pane_repo.repo_name("/Users/x/bare.git"), "bare.git")

    def test_strips_control_chars(self) -> None:
        self.assertEqual(pane_repo.repo_name("/x/bad\x07name/.git"), "badname")


class ResolveLocationTest(TestCase):
    def test_worktree_labels_wt_and_repo_name(self) -> None:
        def runner(_cwd: str) -> str:
            return "/x/pi-kitmux/.git/worktrees/5\n/x/pi-kitmux/.git\n"

        self.assertEqual(pane_repo.resolve_location("/tmp", runner), "pi-kitmux/wt:5")

    def test_main_worktree_labels_main(self) -> None:
        def runner(_cwd: str) -> str:
            return "/x/pi-kitmux/.git\n/x/pi-kitmux/.git\n"

        self.assertEqual(pane_repo.resolve_location("/tmp", runner), "pi-kitmux/main")

    def test_non_git_directory_is_empty(self) -> None:
        self.assertEqual(pane_repo.resolve_location("/tmp", lambda _cwd: ""), "")

    def test_missing_directory_is_empty(self) -> None:
        # Do not call git for a missing path: git -C falls back to the current
        # process cwd, which would misreport a non-repo dir as a repo
        calls: list[str] = []

        def runner(cwd: str) -> str:
            calls.append(cwd)
            return "/x/pi-kitmux/.git\n/x/pi-kitmux/.git\n"

        self.assertEqual(pane_repo.resolve_location("/no/such/dir/xyz", runner), "")
        self.assertEqual(calls, [])

    def test_empty_cwd_is_empty(self) -> None:
        self.assertEqual(
            pane_repo.resolve_location("", lambda _cwd: "/x/r/.git\n/x/r/.git\n"), ""
        )

    def test_result_has_no_control_chars(self) -> None:
        def runner(_cwd: str) -> str:
            return "/x/r/.git/worktrees/bad\x07\n/x/r/.git\n"

        self.assertEqual(pane_repo.resolve_location("/tmp", runner), "r/wt:bad")
