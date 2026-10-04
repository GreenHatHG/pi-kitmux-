"""Regression tests for the pure functions in switch-pi-agent.

The main script name has a hyphen so it cannot be imported directly; load it by
path with importlib. Only covers pure logic that needs no kitty / tmux / fzf.
"""

import importlib.util
from datetime import datetime
from pathlib import Path
from unittest import TestCase, mock

_SCRIPT = Path(__file__).resolve().parent.parent / "switch-pi-agent.py"
_spec = importlib.util.spec_from_file_location("switch_pi_agent", _SCRIPT)
assert (
    _spec is not None and _spec.loader is not None
)  # path always exists; just narrows types
switch_pi_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(switch_pi_agent)


class RenderTabTitleTest(TestCase):
    def test_default_template_returns_title(self) -> None:
        self.assertEqual(
            switch_pi_agent.render_tab_title("{title}", 3, "Pi Agent"), "Pi Agent"
        )

    def test_index_and_slice_placeholders(self) -> None:
        self.assertEqual(
            switch_pi_agent.render_tab_title("{index}: {title[:4]}", 2, "pi-kitmux"),
            "2: pi-k",
        )

    def test_fmt_placeholder_renders_empty(self) -> None:
        self.assertEqual(
            switch_pi_agent.render_tab_title("{fmt.bold}{title}", 1, "x"), "x"
        )

    def test_max_length_truncates_rendered_title(self) -> None:
        self.assertEqual(
            switch_pi_agent.render_tab_title("{title}", 1, "abcdef", maxlen=3), "abc"
        )


class IsPiTest(TestCase):
    def test_matches_pi_binary(self) -> None:
        self.assertTrue(switch_pi_agent.is_pi("pi", "pi"))
        self.assertTrue(switch_pi_agent.is_pi("pi", "/opt/homebrew/bin/pi session --x"))

    def test_matches_pi_coding_agent_command(self) -> None:
        self.assertTrue(switch_pi_agent.is_pi("node", "pi-coding-agent --session x"))

    def test_excludes_itself(self) -> None:
        self.assertFalse(
            switch_pi_agent.is_pi(
                "python3", "/opt/homebrew/bin/python3 ~/.local/bin/switch-pi-agent.py"
            )
        )


class AncestorChainTest(TestCase):
    def test_walks_up_to_init(self) -> None:
        self.assertEqual(
            switch_pi_agent.ancestor_chain(3, {3: 2, 2: 1}), [3, 2]
        )  # the end ancestor launchd(1) is not in the chain

    def test_self_parent_does_not_hang(self) -> None:
        self.assertEqual(switch_pi_agent.ancestor_chain(5, {5: 5}), [5])


def _agent(**overrides: object) -> dict:
    base = {
        "pid": 1,
        "folder": "repo",
        "session": "main",
        "win_name": "pi",
        "win_idx": 1,
        "sess_win_count": 1,
        "pane_cmd": "pi",
        "pi_running": True,
        "pi_done": False,
        "done_at": "",
        "fmt": "pi",
        "where": "main",
        "repo": "repo",
        "pane_tmux_pid": 555,
        "tab_title": "1: repo",
        "tab_id": "10",
        "tab_index": 1,
        "win_index": 1,
        "win_count": 1,
        "pane_id": "%1",
        "win_id": "@1",
        "client_tty": "",
    }
    base.update(overrides)
    return base


class PaneMarkerTest(TestCase):
    def test_running_wins_over_done(self) -> None:
        self.assertEqual(
            switch_pi_agent.pane_marker(_agent(pi_running=True, pi_done=True)), "⏳ "
        )

    def test_done_when_not_running(self) -> None:
        self.assertEqual(
            switch_pi_agent.pane_marker(_agent(pi_running=False, pi_done=True)), "✅ "
        )

    def test_idle_is_empty(self) -> None:
        self.assertEqual(
            switch_pi_agent.pane_marker(_agent(pi_running=False, pi_done=False)), ""
        )


class FormatDoneAtTest(TestCase):
    def test_same_day_is_clock_time(self) -> None:
        now = datetime(2024, 3, 8, 18, 0, 0)
        ts = int(datetime(2024, 3, 8, 14, 32, 5).timestamp())
        self.assertEqual(switch_pi_agent.format_done_at(str(ts), now=now), "14:32:05")

    def test_other_day_adds_date(self) -> None:
        now = datetime(2024, 3, 9, 1, 0, 0)
        ts = int(datetime(2024, 3, 8, 14, 32, 5).timestamp())
        self.assertEqual(
            switch_pi_agent.format_done_at(str(ts), now=now), "03-08 14:32"
        )

    def test_missing_or_bad_value_is_empty(self) -> None:
        self.assertEqual(switch_pi_agent.format_done_at(""), "")
        self.assertEqual(switch_pi_agent.format_done_at("abc"), "")
        self.assertEqual(switch_pi_agent.format_done_at("0"), "")
        self.assertEqual(switch_pi_agent.format_done_at("-5"), "")


class BuildRowsTest(TestCase):
    def test_groups_agents_under_one_tab_header(self) -> None:
        a1 = _agent(pid=101, tab_id="10", tab_title="1: repo")
        a2 = _agent(pid=102, tab_id="10", tab_title="1: repo", win_name="server")
        rows = switch_pi_agent.build_rows([a1, a2])
        self.assertEqual([r["kind"] for r in rows], ["tab", "agent", "agent"])
        self.assertEqual(rows[0]["text"], "1: repo · main")
        self.assertTrue(rows[1]["text"].lstrip().startswith("└"))
        self.assertNotIn("main:", rows[1]["text"])

    def test_group_order_and_no_kitty_last(self) -> None:
        later = _agent(pid=1, tab_id="20", tab_title="2: x", tab_index=2, win_index=1)
        earlier = _agent(pid=2, tab_id="10", tab_title="1: y", tab_index=1, win_index=1)
        none = _agent(
            pid=3, tab_id="", tab_title="No Kitty Tab", tab_index=0, win_index=0
        )
        rows = switch_pi_agent.build_rows([later, none, earlier])
        headers = [r["text"] for r in rows if r["kind"] == "tab"]
        self.assertEqual(headers, ["1: y · main", "2: x · main", "No Kitty Tab"])

    def test_multi_window_header_prefix(self) -> None:
        a = _agent(win_count=2, win_index=2, tab_title="3: repo")
        rows = switch_pi_agent.build_rows([a])
        self.assertEqual(rows[0]["text"], "[win 2] 3: repo · main")

    def test_duplicate_tab_titles_stay_distinct(self) -> None:
        # Two tabs with identical titles still become two separate rows
        # (disambiguated by the returned row number)
        a1 = _agent(pid=11, tab_id="10", tab_title="1: repo", tab_index=1)
        a2 = _agent(pid=22, tab_id="20", tab_title="1: repo", tab_index=2)
        rows = switch_pi_agent.build_rows([a1, a2])
        headers = [r for r in rows if r["kind"] == "tab"]
        self.assertEqual(
            [h["text"] for h in headers], ["1: repo · main", "1: repo · main"]
        )
        self.assertEqual([h["tab_id"] for h in headers], ["10", "20"])

    def test_children_sorted_by_window_index(self) -> None:
        # Sort key is the window index, not the window name (or wt:1 would sort after zsh)
        a1 = _agent(pid=1, win_name="wt:1", win_idx=3)
        a2 = _agent(pid=2, win_name="zsh", win_idx=1)
        a3 = _agent(pid=3, win_name="enclave", win_idx=2)
        rows = switch_pi_agent.build_rows([a1, a2, a3])
        agents = [r["agent"] for r in rows if r["kind"] == "agent"]
        self.assertEqual([a["pid"] for a in agents], [2, 3, 1])

    def test_done_children_float_to_top_within_group(self) -> None:
        # Within a group: ✅ unseen -> ⏳ running -> idle (then by window index within the same state)
        idle = _agent(pid=1, win_idx=1, pi_running=False, pi_done=False)
        done = _agent(pid=2, win_idx=2, pi_running=False, pi_done=True)
        running = _agent(pid=3, win_idx=3, pi_running=True, pi_done=False)
        rows = switch_pi_agent.build_rows([idle, done, running])
        agents = [r["agent"] for r in rows if r["kind"] == "agent"]
        self.assertEqual([a["pid"] for a in agents], [2, 3, 1])


class ParsePanesTest(TestCase):
    """list-panes output parsing: separators and user-editable window names must not clash."""

    # The separator is itself the contract with the tmux query format, so bind it to
    # the module constant on purpose
    SEP = switch_pi_agent._PANE_SEP  # pylint: disable=protected-access

    @staticmethod
    def _record(*overrides: tuple[int, str]) -> str:
        # Field order matches collect_agents' -F query:
        # session / win_id / win_idx / win_name / pane_cmd / @pi_running / @pi_done
        # / session_windows / @pi_win_fmt / pane_current_path / pane_id / pane_pid
        # / @pi_done_at
        fields = [
            "win-5",
            "@32",
            "4",
            "wt:2",
            "enclave",
            "1",
            "",
            "4",
            "pi",
            "/repo/.worktrees/2",
            "%32",
            "28556",
            "",
        ]
        for index, value in overrides:
            fields[index] = value
        # Match the real query format: also end with a separator (the record-boundary
        # newline lands at the next field's start)
        return ParsePanesTest.SEP.join(fields) + ParsePanesTest.SEP

    def test_parses_all_fields(self) -> None:
        panes = switch_pi_agent.parse_panes(self._record() + "\n")
        pane = panes[28556]
        self.assertEqual(pane["session"], "win-5")
        self.assertEqual(pane["win_idx"], 4)
        self.assertTrue(pane["pi_running"])
        self.assertFalse(pane["pi_done"])
        self.assertEqual(pane["sess_win_count"], 4)
        self.assertEqual(pane["fmt"], "pi")
        self.assertEqual(pane["pane_path"], "/repo/.worktrees/2")

    def test_parses_done_time(self) -> None:
        panes = switch_pi_agent.parse_panes(self._record((6, "1"), (12, "1700000000")))
        self.assertEqual(panes[28556]["done_at"], "1700000000")

    def test_parses_done_flag(self) -> None:
        panes = switch_pi_agent.parse_panes(self._record((5, ""), (6, "1")))
        self.assertFalse(panes[28556]["pi_running"])
        self.assertTrue(panes[28556]["pi_done"])

    def test_window_name_with_colon_separator_is_not_dropped(self) -> None:
        # The old version split on ":::" and required an exact field count; a window
        # name with ":::" silently dropped the row
        line = self._record((3, "a:::b"), (8, "a:::b"), (9, "/repo/a:::b"))
        panes = switch_pi_agent.parse_panes(line)
        self.assertEqual(panes[28556]["win_name"], "a:::b")
        self.assertEqual(panes[28556]["fmt"], "a:::b")
        self.assertEqual(panes[28556]["pane_path"], "/repo/a:::b")

    def test_window_name_with_newline_does_not_shift_records(self) -> None:
        # A newline would split one row in two; it must only be scrubbed from the
        # window name, with later records still aligned
        output = (
            self._record((3, "a\nb"))
            + "\n"
            + self._record((0, "win-9"), (3, "zsh"), (10, "%36"), (11, "56470"))
        )
        panes = switch_pi_agent.parse_panes(output)
        self.assertEqual(panes[28556]["win_name"], "ab")
        self.assertEqual(panes[56470]["session"], "win-9")
        self.assertEqual(panes[56470]["win_name"], "zsh")

    def test_ignores_empty_and_malformed_trailing_fields(self) -> None:
        self.assertEqual(switch_pi_agent.parse_panes(""), {})
        self.assertEqual(switch_pi_agent.parse_panes("\n"), {})
        # A short leftover row must not crash or produce half a pane
        self.assertEqual(switch_pi_agent.parse_panes("a\x1fb\x1fc"), {})

    def test_multiple_records_keep_their_own_fields(self) -> None:
        output = (
            self._record()
            + "\n"
            + self._record((0, "win-9"), (3, "zsh"), (10, "%36"), (11, "56470"))
        )
        panes = switch_pi_agent.parse_panes(output)
        self.assertEqual(sorted(panes), [28556, 56470])
        self.assertEqual(panes[28556]["session"], "win-5")
        self.assertEqual(panes[56470]["session"], "win-9")
        self.assertEqual(panes[56470]["win_name"], "zsh")


class WinLabelTest(TestCase):
    """Window label lines up with the tmux status bar @pi_win_fmt.

    The name comes from tmux, the place from the helper.
    """

    def test_non_enclave_uses_rendered_name_and_helper_where(self) -> None:
        self.assertEqual(
            switch_pi_agent.win_label(_agent(pane_cmd="zsh", fmt="zsh")),
            "zsh@main",
        )

    def test_non_git_directory_has_no_where_suffix(self) -> None:
        self.assertEqual(
            switch_pi_agent.win_label(_agent(pane_cmd="zsh", fmt="zsh", where="")),
            "zsh",
        )

    def test_worktree_where_is_appended_after_name(self) -> None:
        self.assertEqual(
            switch_pi_agent.win_label(_agent(fmt="pi", where="wt:2")), "pi@wt:2"
        )

    def test_enclave_asks_helper_for_name(self) -> None:
        # In list-panes the name part of @pi_win_fmt is empty (#() does not run), so
        # the helper fills in the app name
        agent = _agent(pane_cmd="enclave", fmt="", win_name="wt:2", where="wt:2")
        with mock.patch.object(switch_pi_agent, "run", return_value="pi\n"):
            self.assertEqual(switch_pi_agent.win_label(agent), "pi@wt:2")

    def test_enclave_in_pane_mode_keeps_rendered_window_name(self) -> None:
        # In pane_in_mode @pi_win_fmt is just #W, fmt is non-empty, so the helper is not called
        agent = _agent(pane_cmd="enclave", fmt="enclave", win_name="enclave")
        with mock.patch.object(switch_pi_agent, "run") as runner:
            self.assertEqual(switch_pi_agent.win_label(agent), "enclave@main")
        runner.assert_not_called()

    def test_helper_failure_falls_back_to_window_name(self) -> None:
        agent = _agent(pane_cmd="enclave", fmt="", win_name="enclave", where="main")
        with mock.patch.object(switch_pi_agent, "run", return_value=""):
            self.assertEqual(switch_pi_agent.win_label(agent), "enclave@main")


class PaneLocationTest(TestCase):
    def test_splits_helper_output_into_where_and_repo(self) -> None:
        with mock.patch.object(switch_pi_agent, "run", return_value="pi-kitmux/wt:5\n"):
            self.assertEqual(
                switch_pi_agent.pane_location("/x/pi-kitmux/.worktrees/5"),
                ("wt:5", "pi-kitmux"),
            )

    def test_non_git_output_is_empty(self) -> None:
        with mock.patch.object(switch_pi_agent, "run", return_value=""):
            self.assertEqual(switch_pi_agent.pane_location("/tmp"), ("", ""))

    def test_output_without_separator_is_empty(self) -> None:
        # If the helper is missing or outputs junk, do not treat a half result as a place
        with mock.patch.object(switch_pi_agent, "run", return_value="garbage"):
            self.assertEqual(switch_pi_agent.pane_location("/tmp"), ("", ""))


class CwdSuffixTest(TestCase):
    def test_project_root_omits_repeated_basename(self) -> None:
        self.assertEqual(
            switch_pi_agent.cwd_suffix("repo", "pi@main", "repo", True), ""
        )

    def test_worktree_root_omits_repeated_basename(self) -> None:
        self.assertEqual(switch_pi_agent.cwd_suffix("1", "pi@wt:1", "repo", True), "")

    def test_worktree_subdirectory_keeps_cwd(self) -> None:
        self.assertEqual(
            switch_pi_agent.cwd_suffix("src", "pi@wt:1", "repo", True), "cwd:src"
        )

    def test_project_subdirectory_keeps_cwd(self) -> None:
        self.assertEqual(
            switch_pi_agent.cwd_suffix("src", "pi@main", "repo", True), "cwd:src"
        )

    def test_no_kitty_tab_keeps_cwd(self) -> None:
        self.assertEqual(
            switch_pi_agent.cwd_suffix("repo", "pi@main", "repo", False), "cwd:repo"
        )

    def test_missing_repo_keeps_cwd(self) -> None:
        self.assertEqual(
            switch_pi_agent.cwd_suffix("repo", "pi@main", "", True), "cwd:repo"
        )

    def test_label_names_with_at_sign_use_last_separator(self) -> None:
        self.assertEqual(
            switch_pi_agent.cwd_suffix("1", "worker@wt:1", "repo", True), ""
        )


class FormatChildTest(TestCase):
    def test_mirrors_status_bar_cell(self) -> None:
        # "index + ⏳ + name@place", same shape as tmux.conf's window-status-format
        agent = _agent(pid=42, session="win-5", win_idx=2, sess_win_count=4)
        text, ansi = switch_pi_agent.format_child(agent, show_session=False)
        self.assertTrue(text.startswith("   └ 42     2: ⏳ pi@main"))
        self.assertIn("\x1b[33m⏳ \x1b[0mpi@main", ansi)

    def test_index_prefix_hidden_for_single_window_session(self) -> None:
        text, _ = switch_pi_agent.format_child(
            _agent(sess_win_count=1, win_idx=7), show_session=False
        )
        self.assertIn("     ⏳", text)
        self.assertNotIn("main:", text)
        self.assertNotIn("7:", text)

    def test_done_pane_shows_check_marker(self) -> None:
        agent = _agent(pi_running=False, pi_done=True)
        text, ansi = switch_pi_agent.format_child(agent, show_session=False)
        self.assertIn("✅ pi@main", text)
        self.assertIn("\x1b[33m✅ \x1b[0mpi@main", ansi)

    def test_done_pane_shows_completion_time(self) -> None:
        now = datetime.now()
        ts = int(now.replace(hour=14, minute=32, second=5, microsecond=0).timestamp())
        agent = _agent(pi_running=False, pi_done=True, done_at=str(ts))
        text, ansi = switch_pi_agent.format_child(agent, show_session=False)
        self.assertIn("✅ pi@main (14:32:05)", text)
        self.assertIn("\x1b[2m (14:32:05)\x1b[0m", ansi)

    def test_running_pane_hides_completion_time(self) -> None:
        agent = _agent(pi_running=True, pi_done=True, done_at="1700000000")
        text, _ = switch_pi_agent.format_child(agent, show_session=False)
        self.assertNotIn("(", text)
        self.assertIn("⏳ pi@main", text)

    def test_empty_marker_adds_no_color_codes(self) -> None:
        agent = _agent(pi_running=False, pi_done=False)
        text, ansi = switch_pi_agent.format_child(agent, show_session=False)
        self.assertTrue(text.endswith("pi@main"))
        self.assertNotIn("cwd:", text)
        self.assertNotIn("\x1b[33m", ansi)

    def test_no_kitty_tab_keeps_session_in_child(self) -> None:
        agent = _agent(folder="repo", pi_running=False, tab_id="")
        text, _ = switch_pi_agent.format_child(agent, show_session=True)
        self.assertIn("main: pi@main", text)

    def test_worktree_root_does_not_repeat_folder(self) -> None:
        agent = _agent(folder="1", where="wt:1", pi_running=False)
        text, ansi = switch_pi_agent.format_child(agent)
        self.assertTrue(text.endswith("pi@wt:1"))
        self.assertTrue(ansi.endswith("pi@wt:1"))
        self.assertNotIn("cwd:", text)

    def test_worktree_subdirectory_labels_folder(self) -> None:
        agent = _agent(folder="src", where="wt:1", pi_running=False)
        text, _ = switch_pi_agent.format_child(agent)
        self.assertTrue(text.endswith("pi@wt:1  cwd:src"))

    def test_project_subdirectory_labels_folder(self) -> None:
        agent = _agent(folder="src", pi_running=False)
        text, _ = switch_pi_agent.format_child(agent)
        self.assertTrue(text.endswith("pi@main  cwd:src"))

    def test_no_kitty_tab_keeps_folder_context(self) -> None:
        agent = _agent(folder="repo", pi_running=False, tab_id="")
        text, _ = switch_pi_agent.format_child(agent)
        self.assertTrue(text.endswith("pi@main  cwd:repo"))

    def test_non_git_directory_shows_no_where(self) -> None:
        agent = _agent(folder="downloads", where="", pi_running=False, tab_id="")
        text, _ = switch_pi_agent.format_child(agent)
        self.assertTrue(text.endswith("pi  cwd:downloads"))
