"""switch-pi-agent 纯函数的回归测试。

主脚本文件名含连字符无法直接 import，通过 importlib 按路径加载。
只覆盖不依赖 kitty / tmux / fzf 运行环境的纯逻辑。
"""

import importlib.util
from pathlib import Path
from unittest import TestCase, mock

_SCRIPT = Path(__file__).resolve().parent.parent / "switch-pi-agent.py"
_spec = importlib.util.spec_from_file_location("switch_pi_agent", _SCRIPT)
assert _spec is not None and _spec.loader is not None  # 路径固定存在，仅为类型收窄
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
        )  # 终点祖先 launchd(1) 不入链

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
        "pi_win": "⏳ ",
        "bell": False,
        "act": False,
        "fmt": "pi@main",
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


class BuildRowsTest(TestCase):
    def test_groups_agents_under_one_tab_header(self) -> None:
        a1 = _agent(pid=101, tab_id="10", tab_title="1: repo")
        a2 = _agent(pid=102, tab_id="10", tab_title="1: repo", win_name="server")
        rows = switch_pi_agent.build_rows([a1, a2])
        self.assertEqual([r["kind"] for r in rows], ["tab", "agent", "agent"])
        self.assertEqual(rows[0]["text"], "1: repo")
        self.assertTrue(rows[1]["text"].lstrip().startswith("└"))

    def test_group_order_and_no_kitty_last(self) -> None:
        later = _agent(pid=1, tab_id="20", tab_title="2: x", tab_index=2, win_index=1)
        earlier = _agent(pid=2, tab_id="10", tab_title="1: y", tab_index=1, win_index=1)
        none = _agent(
            pid=3, tab_id="", tab_title="No Kitty Tab", tab_index=0, win_index=0
        )
        rows = switch_pi_agent.build_rows([later, none, earlier])
        headers = [r["text"] for r in rows if r["kind"] == "tab"]
        self.assertEqual(headers, ["1: y", "2: x", "No Kitty Tab"])

    def test_multi_window_header_prefix(self) -> None:
        a = _agent(win_count=2, win_index=2, tab_title="3: repo")
        rows = switch_pi_agent.build_rows([a])
        self.assertEqual(rows[0]["text"], "[win 2] 3: repo")

    def test_duplicate_tab_titles_stay_distinct(self) -> None:
        # 两个 tab 渲染出完全相同的标题时，仍是两条独立行（由行号回传消歧义）
        a1 = _agent(pid=11, tab_id="10", tab_title="1: repo", tab_index=1)
        a2 = _agent(pid=22, tab_id="20", tab_title="1: repo", tab_index=2)
        rows = switch_pi_agent.build_rows([a1, a2])
        headers = [r for r in rows if r["kind"] == "tab"]
        self.assertEqual([h["text"] for h in headers], ["1: repo", "1: repo"])
        self.assertEqual([h["tab_id"] for h in headers], ["10", "20"])

    def test_children_sorted_by_window_index(self) -> None:
        # 排序键是窗口序号，不是窗口名字母序（否则 wt:1 会排到 zsh 后面）
        a1 = _agent(pid=1, win_name="wt:1", win_idx=3)
        a2 = _agent(pid=2, win_name="zsh", win_idx=1)
        a3 = _agent(pid=3, win_name="enclave", win_idx=2)
        rows = switch_pi_agent.build_rows([a1, a2, a3])
        agents = [r["agent"] for r in rows if r["kind"] == "agent"]
        self.assertEqual([a["pid"] for a in agents], [2, 3, 1])


class ParsePanesTest(TestCase):
    """list-panes 输出解析：分隔符与用户可改的窗口名不能互相干扰。"""

    # 分隔符本身就是与 tmux 查询格式的契约，故意与模块常量绑定
    SEP = switch_pi_agent._PANE_SEP  # pylint: disable=protected-access

    @staticmethod
    def _record(*overrides: tuple[int, str]) -> str:
        fields = [
            "win-5",
            "@32",
            "4",
            "wt:2",
            "enclave",
            "⏳ ",
            "1",
            "1",
            "4",
            "@wt:2",
            "%32",
            "28556",
        ]
        for index, value in overrides:
            fields[index] = value
        # 与真实查询格式一致：末尾也带一个分隔符（记录边界的换行落在下一字段开头）
        return ParsePanesTest.SEP.join(fields) + ParsePanesTest.SEP

    def test_parses_all_fields(self) -> None:
        panes = switch_pi_agent.parse_panes(self._record() + "\n")
        pane = panes[28556]
        self.assertEqual(pane["session"], "win-5")
        self.assertEqual(pane["win_idx"], 4)
        self.assertEqual(pane["pi_win"], "⏳ ")  # 尾随空格保留
        self.assertTrue(pane["bell"])
        self.assertTrue(pane["act"])
        self.assertEqual(pane["sess_win_count"], 4)
        self.assertEqual(pane["fmt"], "@wt:2")

    def test_window_name_with_colon_separator_is_not_dropped(self) -> None:
        # 旧实现用 ":::" 分隔且要求字段数恰好 12，窗口名含 ":::" 会静默丢行
        line = self._record((3, "a:::b"), (9, "a:::b@main"))
        panes = switch_pi_agent.parse_panes(line)
        self.assertEqual(panes[28556]["win_name"], "a:::b")
        self.assertEqual(panes[28556]["fmt"], "a:::b@main")

    def test_window_name_with_newline_does_not_shift_records(self) -> None:
        # 换行会把一行撑成两行；它应只被从窗口名里抹掉，后续记录不能错位
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
        # 不足 12 个字段的残行不应崩，也不应产出半个 pane
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
    """窗口标签应与 tmux 状态栏 @pi_win_fmt 对齐，且不重抄 tmux 侧逻辑。"""

    def test_non_enclave_uses_expanded_fmt_as_is(self) -> None:
        self.assertEqual(
            switch_pi_agent.win_label(_agent(pane_cmd="zsh", fmt="zsh@main")),
            "zsh@main",
        )

    def test_enclave_asks_helper_and_keeps_tmux_where_suffix(self) -> None:
        # @pi_win_fmt 在 list-panes 里只剩 "@wt:2"，应用名由 helper 补在 @ 之前
        agent = _agent(pane_cmd="enclave", fmt="@wt:2", win_name="wt:2")
        with mock.patch.object(switch_pi_agent, "run", return_value="pi\n"):
            self.assertEqual(switch_pi_agent.win_label(agent), "pi@wt:2")

    def test_enclave_without_where_suffix_is_left_alone(self) -> None:
        # pane_in_mode 时 @pi_win_fmt 就是 #W（无 @），不应硬塞应用名
        agent = _agent(pane_cmd="enclave", fmt="enclave", win_name="enclave")
        with mock.patch.object(switch_pi_agent, "run") as runner:
            self.assertEqual(switch_pi_agent.win_label(agent), "enclave")
        runner.assert_not_called()

    def test_helper_failure_falls_back_to_window_name(self) -> None:
        agent = _agent(pane_cmd="enclave", fmt="@main", win_name="enclave")
        with mock.patch.object(switch_pi_agent, "run", return_value=""):
            self.assertEqual(switch_pi_agent.win_label(agent), "enclave@main")


class FormatChildTest(TestCase):
    def test_mirrors_status_bar_cell(self) -> None:
        # 「序号 + ⏳ + 名称@位置 + 灯」，与 tmux.conf 的 window-status-format 同构
        agent = _agent(
            pid=42,
            session="win-5",
            win_idx=2,
            sess_win_count=4,
            pi_win="⏳ ",
            fmt="pi@main",
            act=True,
        )
        text, ansi = switch_pi_agent.format_child(agent)
        self.assertTrue(text.startswith("   └ 42     win-5:2: ⏳ pi@main ●"))
        self.assertIn("\x1b[33m⏳ \x1b[0mpi@main", ansi)
        self.assertIn("\x1b[36m●\x1b[0m", ansi)  # activity = cyan

    def test_index_prefix_hidden_for_single_window_session(self) -> None:
        text, _ = switch_pi_agent.format_child(_agent(sess_win_count=1, win_idx=7))
        self.assertIn("main: ", text)
        self.assertNotIn("main:7", text)

    def test_bell_wins_over_activity(self) -> None:
        agent = _agent(bell=True, act=True, pi_win="✅ ")
        text, ansi = switch_pi_agent.format_child(agent)
        self.assertIn("◉", text)
        self.assertNotIn("●", text)
        self.assertIn("\x1b[31m◉\x1b[0m", ansi)  # bell = red

    def test_empty_marker_adds_no_color_codes(self) -> None:
        text, ansi = switch_pi_agent.format_child(_agent(pi_win=""))
        self.assertIn("main: pi@main  repo", text)
        self.assertNotIn("\x1b[33m", ansi)
