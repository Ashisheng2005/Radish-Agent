"""第二轮 grep/batch/模块引用工具测试。"""
import os
import sys
import unittest

LLM_SERVER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, LLM_SERVER)

from code_graph.symbol_tools import configure_graph, grep_code, grep_code_batch, list_module_importers
from llmPolling import Polling


class GrepRound2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = os.path.abspath(LLM_SERVER)
        configure_graph(project_path=cls.root)

    def test_grep_per_file_cap(self):
        r = grep_code(pattern=r"config\.yaml", path_glob="**/*.py", max_hits=40, max_hits_per_file=2)
        self.assertTrue(r.get("ok"))
        per_file = {}
        for h in r.get("hits", []):
            per_file[h["file"]] = per_file.get(h["file"], 0) + 1
        self.assertTrue(all(c <= 2 for c in per_file.values()))

    def test_find_config_loader_preset_finds_llm_polling(self):
        r = grep_code(preset="find_config_loader", path_glob="**/*.py")
        self.assertTrue(r.get("ok"))
        files = {h["file"].replace("\\", "/") for h in r.get("hits", [])}
        self.assertTrue(
            any("llmPolling.py" in f for f in files),
            f"expected llmPolling in hits, got {files}",
        )
        self.assertIn("warnings", r)

    def test_grep_batch_by_pattern(self):
        r = grep_code_batch(preset="find_config_loader", path_glob="**/*.py")
        self.assertTrue(r.get("ok"))
        bp = r.get("by_pattern") or {}
        self.assertIn("import_yamlConfig", bp)
        self.assertTrue(any("llmPolling" in (x.get("file") or "") for x in r.get("hits", [])))

    def test_list_module_importers(self):
        r = list_module_importers(module_file="yamlConfig.py", path_glob="**/*.py")
        self.assertTrue(r.get("ok"))
        files = [i["file"] for i in r.get("importers", [])]
        self.assertTrue(any("llmPolling.py" in f for f in files))


class PollingGrepLimitTests(unittest.TestCase):
    def _bot(self):
        bot = Polling.__new__(Polling)
        bot.project_path = LLM_SERVER
        bot._reset_tool_session_state()
        bot._cfg = lambda key, default=None, cast=None: default
        return bot

    def test_grep_session_limit(self):
        bot = self._bot()
        bot._grep_code_count = 2
        block = bot._check_tool_loop_policy("grep_code", "grep::x", '{"pattern":"x"}', {})
        self.assertIsNotNone(block)

    def test_read_symbol_same_file_blocked(self):
        bot = self._bot()
        bot._read_symbol_files.add("yamlconfig.py")
        block = bot._check_read_symbol_loop(
            "read_symbol",
            {"is_native": True, "kwargs": {"file_path": "yamlConfig.py", "symbol": "Config.get"}},
        )
        self.assertIsNotNone(block)


if __name__ == "__main__":
    unittest.main()
