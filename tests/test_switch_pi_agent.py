"""switch-pi-agent 纯函数的回归测试。

主脚本文件名含连字符无法直接 import，通过 importlib 按路径加载。
只覆盖不依赖 kitty / tmux / fzf 运行环境的纯逻辑。
"""

import importlib.util
from pathlib import Path
from unittest import TestCase

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
