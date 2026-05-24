"""console / 命令补全单元测试（不依赖真实终端）。"""
import os
import sys
import unittest

LLM_SERVER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LLM_SERVER)

from prompt_toolkit.document import Document

import console


class _FakeCompleteEvent:
    pass


def _completion_texts(document_text: str) -> list[str]:
    completer = console.build_slash_command_completer()
    if completer is None:
        raise unittest.SkipTest("prompt_toolkit 未安装")
    doc = Document(document_text, cursor_position=len(document_text))
    event = _FakeCompleteEvent()
    results = []
    for item in completer.get_completions(doc, event):
        results.append(item.text)
    return results


class SlashCompleterTests(unittest.TestCase):
    def test_slash_prefix_lists_commands(self):
        texts = _completion_texts("/")
        self.assertTrue(any(t.startswith("/help") for t in texts))
        self.assertTrue(any(t.startswith("/graph") for t in texts))

    def test_partial_graph(self):
        texts = _completion_texts("/gr")
        self.assertTrue(any("graph" in t for t in texts))

    def test_budget_subcommand(self):
        texts = _completion_texts("/budget ro")
        self.assertTrue(any("rounds" in t for t in texts))

    def test_mode_subcommand(self):
        texts = _completion_texts("/mode ")
        self.assertTrue(any("ask" in t for t in texts))
        self.assertTrue(any("plan" in t for t in texts))

    def test_non_slash_no_completions(self):
        completer = console.build_slash_command_completer()
        if completer is None:
            self.skipTest("prompt_toolkit 未安装")
        doc = Document("hello", cursor_position=5)
        items = list(completer.get_completions(doc, _FakeCompleteEvent()))
        self.assertEqual(items, [])

    def test_all_commands_have_meta(self):
        missing = [cmd for cmd in console.COMMANDS if cmd not in console.COMMAND_META]
        self.assertEqual(missing, [], f"缺少补全说明: {missing}")

    def test_completion_status_enabled_when_ptk_present(self):
        if console.PromptSession is None:
            self.skipTest("prompt_toolkit 未安装")
        line = console._completion_status_line()
        self.assertIn("已启用", line)


if __name__ == "__main__":
    unittest.main()
