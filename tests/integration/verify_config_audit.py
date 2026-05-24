"""验收：config 调查工具链能否发现 llmPolling 对 Config 的引用。"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_SERVER = REPO_ROOT / "llmServer"

sys.path.insert(0, str(LLM_SERVER))

from code_graph.symbol_tools import configure_graph, grep_code_batch, list_module_importers


def main():
    configure_graph(project_path=str(LLM_SERVER))
    batch = grep_code_batch(preset="find_config_loader", path_glob="**/*.py")
    importers = list_module_importers(module_file="yamlConfig.py", path_glob="**/*.py")

    hits = batch.get("hits", [])
    polling_hits = [h for h in hits if "llmPolling.py" in (h.get("file") or "")]
    importer_files = [i["file"] for i in importers.get("importers", [])]
    polling_import = any("llmPolling.py" in f for f in importer_files)

    ok = bool(polling_hits) and polling_import
    print("grep_code_batch ok:", batch.get("ok"), "total:", batch.get("total_count"))
    print("llmPolling grep hits:", len(polling_hits))
    for h in polling_hits[:5]:
        print(" ", h.get("line"), h.get("text", "")[:80])
    print("list_module_importers llmPolling:", polling_import)
    print("warnings:", batch.get("warnings"))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
