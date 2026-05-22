"""工具循环防刷单元测试。"""
import os
import sys

LLM_SERVER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LLM_SERVER)

import unittest
from llmPolling import Polling


class ToolLoopPolicyTests(unittest.TestCase):
    def test_duplicate_signature_blocked(self):
        bot = Polling.__new__(Polling)
        bot._reset_tool_session_state()
        sig = 'search_symbols::{"query": "Config"}'
        bot._session_tool_signatures.append(sig)
        block = bot._check_tool_loop_policy("search_symbols", sig, '{"query":"Config"}')
        self.assertIsNotNone(block)
        self.assertEqual(block.get("error_type"), "duplicate_loop")

    def test_findstr_cmd_limited(self):
        bot = Polling.__new__(Polling)
        bot._reset_tool_session_state()
        bot._cmd_findstr_count = 1
        block = bot._check_tool_loop_policy("cmd", "cmd::findstr x", "findstr x")
        self.assertIsNotNone(block)

    def _minimal_bot(self):
        bot = Polling.__new__(Polling)
        bot.project_path = LLM_SERVER
        bot._reset_tool_session_state()
        bot._cfg = lambda key, default=None, cast=None: default
        return bot

    def test_grep_limit_blocks_third(self):
        bot = self._minimal_bot()
        bot._grep_code_count = 2
        block = bot._check_tool_loop_policy("grep_code", "g::a", "{}", {})
        self.assertIsNotNone(block)
        self.assertEqual(block.get("error_type"), "duplicate_loop")


if __name__ == "__main__":
    unittest.main()
