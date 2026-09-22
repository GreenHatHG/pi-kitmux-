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
        self.assertTrue(
            switch_pi_agent.is_pi("pi", "/opt/homebrew/bin/pi session --x")
        )

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
