"""Prompt 前缀缓存布局单元测试。"""
import json
import os
import sys
import unittest

LLM_SERVER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LLM_SERVER)

from deepseek import DeepSeek
from llmPolling import Polling
from promptTemplate import SESSION_SUMMARY_TAG


def _minimal_polling() -> Polling:
    bot = Polling.__new__(Polling)
    bot.last_intent_mode = "ask"
    bot.history_limit = 20
    bot.context = []
    bot.context_summary = ""
    bot.project_path = LLM_SERVER
    bot.language = "Chinese"
    bot._cached_system_prompt = {}
    bot.system_prompt = "fallback"
    bot._cfg = lambda key, default=None, cast=None: default
    bot.enable_wiki_retrieval = False
    bot.project_wiki_json_path = ""
    bot.wiki_mode = "index_only"
    bot.wiki_retrieval_top_k = 3
    bot.context_summary_max_chars = 800
    bot.context_summary_mode = "heuristic"
    bot.context_summary_llm_max_tokens = 400
    bot.metrics_totals = {
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "last_cache_hit_rate": None,
        "last_cached_tokens": 0,
        "last_prompt_tokens": 0,
        "cache_reported": False,
    }
    bot.metrics_rounds = []
    bot._usage_turn_start_idx = 0
    bot._turn_slice_start_idx = None
    bot.rebuild_static_system_prompts()
    return bot


class PromptCacheLayoutTests(unittest.TestCase):
    def test_static_system_prefix_stable_across_builds(self):
        bot = _minimal_polling()
        first = bot._build_messages(intent_mode="ask")
        second = bot._build_messages(intent_mode="ask")
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["role"], "system")
        self.assertEqual(first[0]["content"], second[0]["content"])

    def test_single_system_no_duplicate_static_in_user_task(self):
        bot = _minimal_polling()
        task = bot._build_user_prompt("查找 Config 加载逻辑", "ask")
        bot.context.append({"role": "user", "content": task})
        messages = bot._build_messages(intent_mode="ask")
        systems = [m for m in messages if m.get("role") == "system"]
        self.assertEqual(len(systems), 1)
        self.assertNotIn("Mode objective", task)
        self.assertNotIn("Shared tool policy", task)
        self.assertTrue(task.startswith("[Task]"))

    def test_summary_before_current_task(self):
        bot = _minimal_polling()
        bot.context_summary = "older turns compressed"
        bot.context = [
            {"role": "user", "content": "[Task]\nfirst question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "[Task]\ncurrent question"},
        ]
        messages = bot._build_messages(intent_mode="ask")
        summary_idx = next(
            i for i, m in enumerate(messages) if str(m.get("content", "")).startswith(SESSION_SUMMARY_TAG)
        )
        task_idx = next(
            i for i, m in enumerate(messages) if m.get("content") == "[Task]\ncurrent question"
        )
        self.assertLess(summary_idx, task_idx)

    def test_tool_round_has_one_system_only(self):
        bot = _minimal_polling()
        bot.context = [
            {"role": "user", "content": "[Task]\ninvestigate"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "grep_code", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ]
        messages = bot._build_messages(intent_mode="ask")
        self.assertEqual(sum(1 for m in messages if m.get("role") == "system"), 1)

    def test_usage_cache_fields(self):
        fields = Polling._usage_cache_fields(
            {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {"cached_tokens": 900},
            }
        )
        self.assertEqual(fields["cached_tokens"], 900)
        self.assertAlmostEqual(fields["cache_hit_rate"], 0.9)

    def test_usage_cache_fields_deepseek_hit_tokens(self):
        fields = Polling._usage_cache_fields(
            {
                "prompt_tokens": 1000,
                "prompt_cache_hit_tokens": 850,
                "prompt_cache_miss_tokens": 150,
            }
        )
        self.assertEqual(fields["cached_tokens"], 850)
        self.assertAlmostEqual(fields["cache_hit_rate"], 0.85)

    def test_deepseek_normalize_usage(self):
        raw = {"prompt_tokens": 500, "prompt_tokens_details": {"cached_tokens": 400}}
        out = DeepSeek._normalize_usage_dict(raw)
        self.assertEqual(out["cached_tokens"], 400)

    def test_deepseek_normalize_prompt_cache_hit_tokens(self):
        raw = {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 920,
            "prompt_cache_miss_tokens": 80,
        }
        out = DeepSeek._normalize_usage_dict(raw)
        self.assertEqual(out["cached_tokens"], 920)

    def test_turn_slice_frozen_while_context_grows(self):
        bot = _minimal_polling()
        bot.context = [{"role": "user", "content": f"msg-{i}"} for i in range(45)]
        bot._begin_turn_context_slice()
        first_head = bot._get_context_slice_for_messages()[0]
        bot.context.append({"role": "assistant", "content": "a1"})
        bot.context.append({"role": "tool", "content": "{}"})
        mid = bot._get_context_slice_for_messages()
        bot.context.append({"role": "assistant", "content": "a2"})
        tail = bot._get_context_slice_for_messages()
        self.assertEqual(mid[0], first_head)
        self.assertEqual(tail[0], first_head)
        self.assertEqual(len(tail), len(mid) + 1)

    def test_sliding_slice_shifts_without_turn_freeze(self):
        bot = _minimal_polling()
        bot.context = [{"role": "user", "content": f"msg-{i}"} for i in range(45)]
        bot._turn_slice_start_idx = None
        head_before = bot._get_context_slice_for_messages()[0]
        bot.context.append({"role": "user", "content": "extra"})
        head_after = bot._get_context_slice_for_messages()[0]
        self.assertNotEqual(head_before["content"], head_after["content"])

    def test_finalize_turn_clears_slice_freeze(self):
        bot = _minimal_polling()
        bot.context = [{"role": "user", "content": "x"}]
        bot._turn_slice_start_idx = 0
        bot._finalize_turn_context()
        self.assertIsNone(bot._turn_slice_start_idx)

    def test_heuristic_summary_unchanged(self):
        bot = _minimal_polling()
        bot.history_limit = 2
        bot.context_summary_mode = "heuristic"
        bot.context = [
            {"role": "user", "content": "old-1"},
            {"role": "assistant", "content": "old-2"},
            {"role": "user", "content": "[Task]\nnew"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "tail"},
        ]
        bot._maybe_update_context_summary()
        self.assertIn("old-1", bot.context_summary)
        self.assertEqual(len(bot.context), 4)

    def test_usage_badge_shows_cache_rate(self):
        bot = _minimal_polling()
        bot.metrics_totals["total_tokens"] = 55_900
        bot.metrics_totals["last_cache_hit_rate"] = 0.82
        self.assertIn("cache", bot.get_usage_badge())
        self.assertIn("82%", bot.get_usage_badge())

    def test_append_tool_result_stable_json(self):
        bot = Polling.__new__(Polling)
        bot.context = []
        bot._round_responded_tool_ids = set()
        bot._append_tool_result(
            {"id": "x", "name": "list_dir", "args": "{}", "is_native": True},
            {"ok": True, "tool": "list_dir"},
        )
        payload = json.loads(bot.context[0]["content"])
        self.assertEqual(list(payload.keys()), ["args", "result", "tool"])


if __name__ == "__main__":
    unittest.main()
