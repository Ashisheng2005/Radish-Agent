"""Console 代码图集成冒烟验证（无需 LLM API）。"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_SERVER = REPO_ROOT / "llmServer"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "code_graph_mini"

sys.path.insert(0, str(LLM_SERVER))


def main():
    from code_graph.indexer import CodeGraphIndexer
    from code_graph.gate import SymbolReadGate
    from tools import init_code_graph
    from code_graph.symbol_tools import search_symbols, read_symbol, write_symbol

    class FakeBot:
        def __init__(self):
            self.project_path = str(FIXTURES)
            self.project_code_graph_json_path = ""

        def refresh_code_graph(self, project_path=None):
            from code_graph.store import CodeGraphStore
            import json
            from code_graph.models import CodeGraphIndex

            self.project_path = os.path.abspath(project_path or FIXTURES)
            init_code_graph(self.project_path, self.project_code_graph_json_path)
            resolved = CodeGraphStore.resolve_graph_path(self.project_path, "")
            store = CodeGraphStore(self.project_path)
            status = {
                "loaded": False,
                "path": str(resolved) if resolved else store.graph_path,
                "project_path": self.project_path,
                "node_count": 0,
                "edge_count": 0,
            }
            if resolved and resolved.exists():
                graph = CodeGraphIndex.from_dict(json.loads(resolved.read_text(encoding="utf-8")))
                status.update(
                    {
                        "loaded": True,
                        "node_count": graph.stats.get("node_count", len(graph.nodes)),
                        "edge_count": graph.stats.get("edge_count", len(graph.edges)),
                    }
                )
            return status

        def build_code_graph(self, wiki_root=None):
            index = CodeGraphIndexer(self.project_path, wiki_root=wiki_root).execute()
            status = self.refresh_code_graph()
            status["node_count"] = index.stats.get("node_count", len(index.nodes))
            status["edge_count"] = index.stats.get("edge_count", len(index.edges))
            return status

        def get_code_graph_status(self):
            return self.refresh_code_graph()

    SymbolReadGate.set_active_gate_id("verify")
    SymbolReadGate.get("verify").clear()

    bot = FakeBot()
    st = bot.build_code_graph()
    assert st["node_count"] >= 5, st
    assert st["loaded"], st

    hits = search_symbols("main")
    assert hits["ok"], hits

    read_symbol("sample.py", "helper", include_neighbors=True)
    ws = write_symbol(
        "sample.py",
        "helper",
        edits=[{"op": "replace", "s": 1, "e": 1, "t": "    return 2"}],
        dry_run=True,
    )
    assert ws.get("ok"), ws

    from console import _format_graph_status, _handle_graph_command

    class B:
        get_code_graph_status = bot.get_code_graph_status
        build_code_graph = bot.build_code_graph
        clear_symbol_read_gate = lambda self: SymbolReadGate.get("verify").clear()

    b = B()
    assert "已加载" in _format_graph_status(bot.get_code_graph_status())
    assert _handle_graph_command(b, "/graph status") is True

    print("verify_console_graph: OK")


if __name__ == "__main__":
    main()
