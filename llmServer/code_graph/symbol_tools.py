"""符号级工具：search_symbols、read_symbol、write_symbol。"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from RadishTools.src.FileExecutor.core.WriteFileV2 import write_file_v2_execute

from .gate import SymbolReadGate, neighbor_intent_summary
from .models import CodeGraphIndex, CodeNode
from .store import CodeGraphStore

_active_graph: Optional[CodeGraphIndex] = None
_active_project_path: Optional[str] = None
_configured_graph_path: str = ""


def configure_graph(project_path: Optional[str] = None, graph_path: str = "") -> None:
    global _active_graph, _active_project_path, _configured_graph_path
    _configured_graph_path = graph_path or ""
    _active_project_path = project_path
    _active_graph = None


def get_graph(force_reload: bool = False) -> Optional[CodeGraphIndex]:
    global _active_graph
    if _active_graph is not None and not force_reload:
        return _active_graph
    resolved = CodeGraphStore.resolve_graph_path(_active_project_path, _configured_graph_path)
    if resolved is None:
        return None
    _active_graph = CodeGraphStore.load_from_path(str(resolved))
    return _active_graph


def _resolve_file_abs(file_path: str) -> str:
    if os.path.isabs(file_path):
        return file_path
    if _active_project_path:
        return os.path.normpath(os.path.join(_active_project_path, file_path))
    return os.path.abspath(file_path)


def _rel_path(file_path: str) -> str:
    if _active_project_path and os.path.isabs(file_path):
        try:
            return os.path.relpath(file_path, _active_project_path).replace("\\", "/")
        except ValueError:
            pass
    return file_path.replace("\\", "/")


def _pick_node(graph: CodeGraphIndex, file_path: str, symbol: str) -> Optional[CodeNode]:
    rel = _rel_path(file_path)
    matches = graph.find_nodes_by_symbol(rel, symbol)
    if not matches and file_path:
        matches = graph.find_nodes_by_symbol("", symbol)
        matches = [m for m in matches if m.file_path.endswith(file_path.replace("\\", "/").split("/")[-1])]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    exact = [m for m in matches if m.qualified_name == symbol or m.qualified_name.split(".")[-1] == symbol]
    return exact[0] if len(exact) == 1 else matches[0]


def _read_source(node: CodeNode) -> str:
    abs_path = _resolve_file_abs(node.file_path)
    if not os.path.isfile(abs_path):
        return ""
    with open(abs_path, "r", encoding="utf-8") as fp:
        lines = fp.readlines()
    start = max(1, node.start_line)
    end = min(len(lines), node.end_line)
    return "".join(lines[start - 1 : end])


def _neighbor_ids(graph: CodeGraphIndex, node: CodeNode, upstream_depth: int, downstream_depth: int) -> Dict[str, List[CodeNode]]:
    upstream: List[CodeNode] = []
    downstream: List[CodeNode] = []
    for nid in node.called_by[: max(0, upstream_depth) * 10]:
        n = graph.get_node(nid)
        if n and n not in upstream:
            upstream.append(n)
        if len(upstream) >= upstream_depth:
            break
    for nid in node.calls[: max(0, downstream_depth) * 10]:
        n = graph.get_node(nid) if len(nid) == 16 else None
        if n is None:
            continue
        if n not in downstream:
            downstream.append(n)
        if len(downstream) >= downstream_depth:
            break
    return {"upstream": upstream[:upstream_depth], "downstream": downstream[:downstream_depth]}


def _search_hints(query: str) -> List[str]:
    q = (query or "").strip().lower()
    hints = [
        "尝试 query=Config 或 file_glob=*yaml*",
        "config.yaml 等配置文件不是图节点，请用 grep_code 或 read_file",
        "找到符号后使用 read_symbol(include_neighbors=True) 与 list_symbol_callers",
    ]
    if "config" in q or "yaml" in q:
        hints.insert(0, "推荐: search_symbols('Config', file_glob='*yaml*') 一次即可")
    return hints


def _score_symbol_node(node: CodeNode, terms: Set[str], query: str) -> int:
    hay = " ".join([node.qualified_name, node.file_path, node.summary, node.kind]).lower()
    score = sum(1 for t in terms if t in hay)
    q_lower = query.strip().lower()
    if q_lower and q_lower in node.qualified_name.lower():
        score += 3
    if q_lower and q_lower in node.file_path.lower().replace("\\", "/"):
        score += 2
    return score


def _node_summary(node: CodeNode, source: str = "") -> Dict[str, Any]:
    return {
        "node_id": node.node_id,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "kind": node.kind,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "body_hash": node.body_hash,
        "summary": node.summary,
        "calls_count": len(node.calls),
        "called_by_count": len(node.called_by),
        "source": source,
    }


def search_symbols(query: str, language: Optional[str] = None, file_glob: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    graph = get_graph()
    if graph is None:
        return {
            "ok": False,
            "tool": "search_symbols",
            "error_type": "graph_not_found",
            "error": "未找到 CODE_GRAPH.json，请先运行项目索引：python -m code_graph.build_index <project_path>",
        }

    terms = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+", query or "")}
    q_lower = (query or "").strip().lower()
    results: List[tuple] = []
    for node in graph.nodes:
        if language and node.language != language:
            continue
        if file_glob and not re.search(file_glob.replace("*", ".*"), node.file_path, re.I):
            continue
        score = _score_symbol_node(node, terms, query or "")
        if score <= 0:
            continue
        results.append((score, _node_summary(node)))
    results.sort(key=lambda x: x[0], reverse=True)
    hits = [x[1] for x in results[:limit]]
    payload: Dict[str, Any] = {
        "ok": True,
        "tool": "search_symbols",
        "query": query,
        "count": len(hits),
        "results": hits,
    }
    if not hits:
        payload["hints"] = _search_hints(query)
        payload["next_steps"] = [
            "grep_code(pattern='config.yaml', path_glob='**/*.py')",
            "read_file 读取 yamlConfig.py 等加载模块",
            "list_symbol_callers(file_path='...', symbol='Config')",
        ]
    return payload


def list_symbol_callers(
    file_path: str,
    symbol: str,
    limit: int = 20,
    include_source: bool = False,
) -> Dict[str, Any]:
    """列出代码图中调用目标符号的上游节点（called_by）。"""
    graph = get_graph()
    if graph is None:
        return {
            "ok": False,
            "tool": "list_symbol_callers",
            "error_type": "graph_not_found",
            "error": "未找到 CODE_GRAPH.json，请先 /graph build",
        }

    node = _pick_node(graph, file_path, symbol)
    if node is None:
        return {
            "ok": False,
            "tool": "list_symbol_callers",
            "error_type": "symbol_not_found",
            "error": f"未找到符号: {file_path} :: {symbol}",
            "hint": "先用 search_symbols 定位符号名与 file_path",
        }

    callers: List[Dict[str, Any]] = []
    for nid in node.called_by[: max(1, limit) * 2]:
        caller = graph.get_node(nid)
        if not caller:
            continue
        item = _node_summary(caller, _read_source(caller) if include_source else "")
        callers.append(item)
        if len(callers) >= limit:
            break

    return {
        "ok": True,
        "tool": "list_symbol_callers",
        "target": _node_summary(node),
        "caller_count": len(callers),
        "callers": callers,
        "hint": (
            "仅反映「谁调用了该符号」；Config(...) 实例化在 __init__ 的 called_by 中，"
            "查 .get 无结果时请查 Config.__init__、list_module_importers 或 grep_code_batch。"
        ),
    }


_GREP_IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "wiki", "vendor", "dist", "build", ".idea",
}
_GREP_EXT_ALLOW = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs",
    ".cpp", ".cc", ".cxx", ".hpp", ".h", ".md", ".yaml", ".yml",
}

_CONFIG_LOADER_PRESETS = [
    (r"from\s+yamlConfig|import\s+yamlConfig", "import_yamlConfig"),
    (r"Config\s*\(", "Config_instantiation"),
    (r"config\.yaml", "config_yaml_literal"),
]


def _grep_code_scan(
    pattern: str,
    path_glob: str = "**/*",
    max_hits_total: int = 40,
    max_hits_per_file: int = 3,
    case_insensitive: bool = True,
    root: Optional[str] = None,
) -> Dict[str, Any]:
    root = os.path.abspath(root or _active_project_path or os.getcwd())
    max_hits_total = max(1, min(int(max_hits_total), 200))
    max_hits_per_file = max(1, min(int(max_hits_per_file), 20))

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)
    except re.error as err:
        return {"ok": False, "error": f"无效正则 pattern: {err}"}

    glob_pat = path_glob.replace("\\", "/")

    def _path_matches(rel_path: str, filename: str) -> bool:
        if fnmatch.fnmatch(rel_path, glob_pat) or fnmatch.fnmatch(filename, glob_pat):
            return True
        if glob_pat.endswith("/*.py") or glob_pat == "**/*.py":
            return filename.endswith(".py")
        if "/**/" in glob_pat:
            suffix = glob_pat.split("/**/", 1)[-1]
            if suffix and fnmatch.fnmatch(rel_path, f"*{suffix}"):
                return True
        return False

    hits: List[Dict[str, Any]] = []
    per_file_count: Dict[str, int] = {}
    scanned_files = 0
    stopped_early = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _GREP_IGNORE_DIRS and not d.startswith(".")]
        for filename in filenames:
            if len(hits) >= max_hits_total:
                stopped_early = True
                break
            rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
            rel_path = filename if rel_dir == "." else f"{rel_dir}/{filename}"
            rel_path = rel_path.replace("\\", "/")
            if not _path_matches(rel_path, filename):
                continue
            _, ext = os.path.splitext(filename.lower())
            if ext and ext not in _GREP_EXT_ALLOW:
                continue
            scanned_files += 1
            if per_file_count.get(rel_path, 0) >= max_hits_per_file:
                continue
            abs_path = os.path.join(dirpath, filename)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fp:
                    for line_no, line in enumerate(fp, start=1):
                        if per_file_count.get(rel_path, 0) >= max_hits_per_file:
                            break
                        if regex.search(line):
                            hits.append(
                                {
                                    "file": rel_path,
                                    "line": line_no,
                                    "text": line.rstrip()[:200],
                                }
                            )
                            per_file_count[rel_path] = per_file_count.get(rel_path, 0) + 1
                            if len(hits) >= max_hits_total:
                                stopped_early = True
                                break
            except OSError:
                continue
        if stopped_early:
            break

    truncated = stopped_early
    suggestion = ""
    if truncated:
        suggestion = (
            "结果因 max_hits_total 截断，可能漏掉后续文件。"
            "请缩小 path_glob（如 **/*.py）、提高 max_hits_total，或使用 grep_code_batch / preset=find_config_loader。"
        )
    return {
        "ok": True,
        "pattern": pattern,
        "path_glob": path_glob,
        "project_path": root,
        "count": len(hits),
        "truncated": truncated,
        "scanned_files": scanned_files,
        "hits": hits,
        "suggestion": suggestion,
    }


def _attach_usage_warnings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """若发现 import/实例化引用，提示勿误判为死代码。"""
    hits = payload.get("hits") or []
    by_pattern = payload.get("by_pattern") or {}
    usage_hits = 0
    for item in hits:
        text = str(item.get("text", "")).lower()
        if "import yamlconfig" in text or re.search(r"\bconfig\s*\(", text, re.I):
            usage_hits += 1
    for key, items in by_pattern.items():
        if key in {"import_yamlConfig", "Config_instantiation"} and items:
            usage_hits += len(items)
        for item in items or []:
            text = str(item.get("text", "")).lower()
            if "import yamlconfig" in text or re.search(r"\bconfig\s*\(", text, re.I):
                usage_hits += 1
    if usage_hits > 0:
        payload.setdefault("warnings", []).append(
            "检测到 Config/yamlConfig 的 import 或实例化引用，请勿结论为「全项目未使用/死代码」。"
        )
        payload["usage_reference_count"] = usage_hits
    return payload


def grep_code(
    pattern: str = "",
    path_glob: str = "**/*",
    max_hits: int = 40,
    max_hits_per_file: int = 3,
    case_insensitive: bool = True,
    preset: Optional[str] = None,
) -> Dict[str, Any]:
    """在项目目录内搜索文本模式，替代手写 findstr/cmd。"""
    if preset == "find_config_loader":
        return grep_code_batch(
            patterns=[p for p, _ in _CONFIG_LOADER_PRESETS],
            path_glob=path_glob or "**/*.py",
            max_hits_total=max_hits,
            max_hits_per_file=max_hits_per_file,
            case_insensitive=case_insensitive,
        )

    if not pattern:
        return {
            "ok": False,
            "tool": "grep_code",
            "error_type": "invalid_arguments",
            "error": "pattern 不能为空（或使用 preset='find_config_loader'）",
        }

    scan = _grep_code_scan(
        pattern=pattern,
        path_glob=path_glob,
        max_hits_total=max_hits,
        max_hits_per_file=max_hits_per_file,
        case_insensitive=case_insensitive,
    )
    if not scan.get("ok"):
        return {"ok": False, "tool": "grep_code", "error_type": "invalid_arguments", "error": scan.get("error", "")}

    payload = {
        "ok": True,
        "tool": "grep_code",
        "pattern": pattern,
        "path_glob": path_glob,
        "project_path": scan["project_path"],
        "count": scan["count"],
        "truncated": scan["truncated"],
        "scanned_files": scan["scanned_files"],
        "hits": scan["hits"],
        "suggestion": scan.get("suggestion", ""),
        "hint": "若需符号级上下游，对命中文件再用 read_symbol / list_symbol_callers",
    }
    return _attach_usage_warnings(payload)


def grep_code_batch(
    patterns=None,
    path_glob: str = "**/*.py",
    max_hits_total: int = 40,
    max_hits_per_file: int = 3,
    case_insensitive: bool = True,
    preset: Optional[str] = None,
) -> Dict[str, Any]:
    """一次执行多个 pattern，合并结果，避免连续多次 grep_code。"""
    if preset == "find_config_loader":
        pattern_list = [p for p, _ in _CONFIG_LOADER_PRESETS]
        label_map = {p: label for p, label in _CONFIG_LOADER_PRESETS}
    else:
        if isinstance(patterns, str):
            try:
                pattern_list = json.loads(patterns)
            except json.JSONDecodeError:
                pattern_list = [p.strip() for p in patterns.split(",") if p.strip()]
        else:
            pattern_list = list(patterns or [])
        label_map = {p: p for p in pattern_list}

    if not pattern_list:
        return {
            "ok": False,
            "tool": "grep_code_batch",
            "error_type": "invalid_arguments",
            "error": "patterns 不能为空",
        }

    root = os.path.abspath(_active_project_path or os.getcwd())
    by_pattern: Dict[str, List[Dict[str, Any]]] = {}
    all_hits: List[Dict[str, Any]] = []
    seen = set()
    truncated_any = False
    budget = max(1, min(int(max_hits_total), 200))

    per_pat_budget = max(8, budget // max(1, len(pattern_list)))

    for pat in pattern_list:
        if len(all_hits) >= budget:
            truncated_any = True
            break
        remaining = min(per_pat_budget, budget - len(all_hits))
        scan = _grep_code_scan(
            pattern=pat,
            path_glob=path_glob,
            max_hits_total=remaining,
            max_hits_per_file=max_hits_per_file,
            case_insensitive=case_insensitive,
            root=root,
        )
        if not scan.get("ok"):
            continue
        label = label_map.get(pat, pat)
        items = []
        for h in scan["hits"]:
            key = (h["file"], h["line"], h["text"])
            if key in seen:
                continue
            seen.add(key)
            items.append(h)
            all_hits.append(h)
        by_pattern[label] = items
        if scan.get("truncated"):
            truncated_any = True

    payload = {
        "ok": True,
        "tool": "grep_code_batch",
        "path_glob": path_glob,
        "project_path": root,
        "total_count": len(all_hits),
        "truncated": truncated_any,
        "by_pattern": by_pattern,
        "hits": all_hits,
        "hint": "模块引用请看 by_pattern 中 import_yamlConfig / Config_instantiation",
    }
    return _attach_usage_warnings(payload)


def list_module_importers(
    module_file: str = "yamlConfig.py",
    path_glob: str = "**/*.py",
    limit: int = 20,
) -> Dict[str, Any]:
    """列出 import/引用指定模块的文件（解决「谁用了 yamlConfig」）。"""
    base = os.path.basename(module_file.replace("\\", "/"))
    mod_name = base.replace(".py", "").replace(".java", "")

    batch = grep_code_batch(
        patterns=[
            rf"from\s+{re.escape(mod_name)}\b",
            rf"import\s+{re.escape(mod_name)}\b",
            rf"from\s+{re.escape(base)}\b",
            rf"import\s+{re.escape(base)}\b",
        ],
        path_glob=path_glob,
        max_hits_total=limit * 3,
        max_hits_per_file=5,
    )

    importers: List[Dict[str, Any]] = []
    seen_files: Set[str] = set()
    for label, items in (batch.get("by_pattern") or {}).items():
        for item in items:
            fp = item.get("file", "")
            if fp in seen_files:
                continue
            seen_files.add(fp)
            importers.append(
                {
                    "file": fp,
                    "line": item.get("line"),
                    "text": item.get("text"),
                    "match_type": label,
                }
            )
            if len(importers) >= limit:
                break

    return {
        "ok": True,
        "tool": "list_module_importers",
        "module_file": module_file,
        "importer_count": len(importers),
        "importers": importers[:limit],
        "hint": "实例化请再用 grep_code(preset='find_config_loader') 查看 Config( 或 read_symbol(Config.__init__) 的邻居",
        "warnings": batch.get("warnings", []),
    }


def read_symbol(
    file_path: str,
    symbol: str,
    include_neighbors: bool = True,
    upstream_depth: int = 1,
    downstream_depth: int = 1,
    include_source: bool = True,
) -> Dict[str, Any]:
    graph = get_graph()
    if graph is None:
        return {
            "ok": False,
            "tool": "read_symbol",
            "error_type": "graph_not_found",
            "error": "未找到 CODE_GRAPH.json，请先构建项目代码图索引。",
        }

    node = _pick_node(graph, file_path, symbol)
    if node is None:
        return {
            "ok": False,
            "tool": "read_symbol",
            "error_type": "symbol_not_found",
            "error": f"未找到符号: {file_path} :: {symbol}",
        }

    source = _read_source(node) if include_source else ""
    payload = _node_summary(node, source)
    neighbors = {"upstream": [], "downstream": []}
    neighbor_ids: Set[str] = set()
    nb = {"upstream": [], "downstream": []}

    if include_neighbors:
        nb = _neighbor_ids(graph, node, upstream_depth, downstream_depth)
        up_names = [n.qualified_name for n in nb["upstream"]]
        down_names = [n.qualified_name for n in nb["downstream"]]
        for side in ("upstream", "downstream"):
            for n in nb[side]:
                ns = _read_source(n) if include_source else ""
                item = _node_summary(n, ns)
                neighbors[side].append(item)
                neighbor_ids.add(n.node_id)

    gate = SymbolReadGate.active()
    gate.record_read(node.node_id, neighbor_ids)

    return {
        "ok": True,
        "tool": "read_symbol",
        "symbol": payload,
        "neighbors": neighbors,
        "neighbor_intent": neighbor_intent_summary(
            [n.qualified_name for n in (nb["upstream"] if include_neighbors else [])],
            [n.qualified_name for n in (nb["downstream"] if include_neighbors else [])],
        ),
        "read_recorded": True,
        "required_before_write": [node.node_id] + sorted(neighbor_ids),
    }


def _relative_to_absolute_edits(node: CodeNode, edits_payload: Any) -> List[Dict[str, Any]]:
    if isinstance(edits_payload, str):
        edits_payload = json.loads(edits_payload)
    if isinstance(edits_payload, dict) and "edits" in edits_payload:
        edits_payload = edits_payload["edits"]
    if not isinstance(edits_payload, list):
        raise ValueError("edits 必须是 JSON 数组")

    abs_edits = []
    base = node.start_line
    for idx, item in enumerate(edits_payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"edit #{idx}: 必须是对象")
        rel_s = item.get("start_line", item.get("s"))
        rel_e = item.get("end_line", item.get("e"))
        if rel_s is None:
            raise ValueError(f"edit #{idx}: 缺少相对 start_line/s")
        if not isinstance(rel_s, int):
            raise ValueError(f"edit #{idx}: s 必须是 int")
        abs_s = base + rel_s - 1
        abs_e = None
        if rel_e is not None:
            if not isinstance(rel_e, int):
                raise ValueError(f"edit #{idx}: e 必须是 int")
            abs_e = base + rel_e - 1
        abs_edits.append(
            {
                "op": item.get("op", "replace"),
                "s": abs_s,
                "e": abs_e,
                "t": item.get("new_text", item.get("t", "")),
            }
        )
    return abs_edits


def write_symbol(
    file_path: str,
    symbol: str,
    edits,
    base_body_hash: Optional[str] = None,
    dry_run: bool = False,
    return_patch: bool = True,
    conflict_mode: str = "strict",
    encoding: str = "utf-8",
    skip_gate: bool = False,
    skip_neighbors: Optional[List[str]] = None,
    skip_justification: str = "",
    upstream_depth: int = 1,
    downstream_depth: int = 1,
) -> Dict[str, Any]:
    graph = get_graph()
    if graph is None:
        return {
            "ok": False,
            "tool": "write_symbol",
            "error_type": "graph_not_found",
            "error": "未找到 CODE_GRAPH.json，请降级使用 read_file + write_file。",
        }

    node = _pick_node(graph, file_path, symbol)
    if node is None:
        return {
            "ok": False,
            "tool": "write_symbol",
            "error_type": "symbol_not_found",
            "error": f"未找到符号: {file_path} :: {symbol}",
        }

    if base_body_hash and base_body_hash != node.body_hash:
        current = _read_source(node)
        from .models import body_hash as bh
        if bh(current) != base_body_hash:
            return {
                "ok": False,
                "tool": "write_symbol",
                "error_type": "body_hash_conflict",
                "error": "符号 body_hash 已变化，请重新 read_symbol 后再写入。",
                "expected": base_body_hash,
                "actual": node.body_hash,
            }

    nb = _neighbor_ids(graph, node, upstream_depth, downstream_depth)
    upstream_ids = {n.node_id for n in nb["upstream"]}
    downstream_ids = {n.node_id for n in nb["downstream"]}

    skip_set: Set[str] = set()
    if skip_neighbors:
        for sid in skip_neighbors:
            if sid:
                skip_set.add(str(sid))
        if skip_set and skip_justification:
            gate = SymbolReadGate.active()
            gate.record_skip(skip_set, skip_justification)

    if not skip_gate:
        gate = SymbolReadGate.active()
        missing = gate.required_for_write(node.node_id, upstream_ids, downstream_ids)
        if missing:
            return {
                "ok": False,
                "tool": "write_symbol",
                "error_type": "symbol_not_read",
                "error": "写入前必须先 read_symbol 读取目标及一阶上下游节点。",
                "missing_node_ids": sorted(missing),
                "hint": f"read_symbol(file_path='{node.file_path}', symbol='{node.qualified_name}', include_neighbors=True)",
            }

    try:
        abs_edits = _relative_to_absolute_edits(node, edits)
    except (ValueError, json.JSONDecodeError) as err:
        return {
            "ok": False,
            "tool": "write_symbol",
            "error_type": "invalid_arguments",
            "error": str(err),
        }

    abs_file = _resolve_file_abs(node.file_path)
    result = write_file_v2_execute(
        file_path=abs_file,
        edits=json.dumps(abs_edits, ensure_ascii=False),
        encoding=encoding,
        dry_run=dry_run,
        return_patch=return_patch,
        conflict_mode=conflict_mode,
    )
    if isinstance(result, dict):
        result["tool"] = "write_symbol"
        result["symbol"] = node.qualified_name
        result["node_id"] = node.node_id
        result["relative_edits_converted"] = abs_edits
    return result
