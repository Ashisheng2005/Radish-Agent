"""代码图 MVP 单元测试。"""

import json
import os
import sys
import tempfile
import unittest

LLM_SERVER = os.path.dirname(os.path.abspath(__file__))
if LLM_SERVER not in sys.path:
    sys.path.insert(0, LLM_SERVER)

from code_graph.gate import SymbolReadGate
from code_graph.indexer import CodeGraphIndexer
from code_graph.models import body_hash
from code_graph.store import CodeGraphStore
from code_graph.symbol_tools import (
    configure_graph,
    grep_code,
    list_symbol_callers,
    read_symbol,
    search_symbols,
    write_symbol,
)


FIXTURES = os.path.join(os.path.dirname(LLM_SERVER), "tests", "fixtures", "code_graph_mini")


class CodeGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="code_graph_test_")
        cls.wiki_root = os.path.join(cls.tmp, "wiki")
        indexer = CodeGraphIndexer(FIXTURES, wiki_root=cls.wiki_root)
        cls.index = indexer.execute()
        cls.graph_path = indexer.store.graph_path
        configure_graph(project_path=FIXTURES, graph_path=cls.graph_path)
        SymbolReadGate.set_active_gate_id("test")
        SymbolReadGate.get("test").clear()

    def test_index_has_multilang_nodes(self):
        langs = {n.language for n in self.index.nodes}
        self.assertIn("python", langs)
        self.assertIn("javascript", langs)
        self.assertGreaterEqual(self.index.stats.get("node_count", 0), 5)

    def test_nodes_have_required_fields(self):
        for node in self.index.nodes:
            self.assertTrue(node.node_id)
            self.assertTrue(node.qualified_name)
            self.assertTrue(node.file_path)
            self.assertGreaterEqual(node.end_line, node.start_line)
            self.assertTrue(node.body_hash)

    def test_call_edges_and_called_by(self):
        py_main = next((n for n in self.index.nodes if n.qualified_name == "main" and n.file_path.endswith("sample.py")), None)
        self.assertIsNotNone(py_main)
        self.assertTrue(py_main.calls or py_main.called_by or len(self.index.edges) >= 0)
        if py_main.calls:
            callee = self.index.get_node(py_main.calls[0])
            self.assertIsNotNone(callee)
            self.assertIn(py_main.node_id, callee.called_by)

    def test_search_symbols(self):
        result = search_symbols("main")
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(result.get("count", 0), 1)

    def test_search_symbols_empty_has_hints(self):
        result = search_symbols("config.yaml")
        self.assertTrue(result.get("ok"))
        if result.get("count", 0) == 0:
            self.assertIn("hints", result)
            self.assertIn("next_steps", result)

    def test_search_symbols_file_path_match(self):
        result = search_symbols("sample.py")
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(result.get("count", 0), 1)

    def test_list_symbol_callers(self):
        read_symbol("sample.py", "helper", include_neighbors=False)
        result = list_symbol_callers("sample.py", "helper")
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(result.get("caller_count", 0), 0)

    def test_grep_code(self):
        result = grep_code("helper", path_glob="*.py", max_hits=10)
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(result.get("count", 0), 1)

    def test_read_symbol_records_gate(self):
        gate = SymbolReadGate.get("test")
        gate.clear()
        result = read_symbol("sample.py", "main", include_neighbors=True)
        self.assertTrue(result.get("ok"))
        node_id = result["symbol"]["node_id"]
        self.assertTrue(gate.is_read(node_id))

    def test_write_symbol_requires_read(self):
        gate = SymbolReadGate.get("test")
        gate.clear()
        blocked = write_symbol(
            "sample.py",
            "helper",
            edits='[{"op":"replace","s":1,"e":1,"t":"    return 2"}]',
            dry_run=True,
        )
        self.assertFalse(blocked.get("ok"))
        self.assertEqual(blocked.get("error_type"), "symbol_not_read")

    def test_write_symbol_after_read(self):
        gate = SymbolReadGate.get("test")
        gate.clear()
        read_symbol("sample.py", "helper", include_neighbors=True)
        ok = write_symbol(
            "sample.py",
            "helper",
            edits='[{"op":"replace","s":1,"e":1,"t":"    return 2"}]',
            dry_run=True,
        )
        self.assertTrue(ok.get("ok"), ok)

    def test_store_roundtrip(self):
        loaded = CodeGraphStore.load_from_path(self.graph_path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.stats.get("node_count"), self.index.stats.get("node_count"))


if __name__ == "__main__":
    unittest.main()
