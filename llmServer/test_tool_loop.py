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

    def test_sanitize_tool_chain_inserts_missing_replies(self):
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "grep_code", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "grep_code_batch", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
            {"role": "user", "content": "调查证据已足够"},
        ]
        fixed = Polling._sanitize_tool_message_chain(messages)
        roles = [m["role"] for m in fixed]
        self.assertEqual(
            roles,
            ["user", "assistant", "tool", "tool", "user"],
        )
        self.assertEqual(fixed[3]["tool_call_id"], "c2")

    def test_fill_unanswered_native_tool_calls(self):
        bot = Polling.__new__(Polling)
        bot.context = []
        bot._round_responded_tool_ids = {"call_1"}
        execution_list = [
            {"id": "call_1", "name": "list_dir", "args": "{}", "is_native": True},
            {"id": "call_2", "name": "read_file", "args": "{}", "is_native": True},
            {"id": "call_3", "name": "read_file", "args": "{}", "is_native": True},
        ]
        filled = bot._fill_unanswered_native_tool_calls(execution_list, reason="budget")
        self.assertEqual(filled, 2)
        tool_msgs = [m for m in bot.context if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertEqual({m["tool_call_id"] for m in tool_msgs}, {"call_2", "call_3"})


if __name__ == "__main__":
    unittest.main()
