"""项目级代码图索引器。"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Optional, Set, Tuple

from .models import CodeEdge, CodeGraphIndex, CodeNode, body_hash, make_node_id
from .parser import CODE_EXTENSIONS, detect_language, extract_symbols
from .store import CodeGraphStore


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "site-packages",
    "wiki",
    "wiki_output",
    "vendor",
}


class CodeGraphIndexer:
    def __init__(
        self,
        project_path: str,
        wiki_root: Optional[str] = None,
        ignore_dirs: Optional[Set[str]] = None,
    ):
        self.project_path = os.path.abspath(project_path)
        self.store = CodeGraphStore(self.project_path, wiki_root)
        self.ignore_dirs = set(ignore_dirs or []) | DEFAULT_IGNORE_DIRS

    def execute(self) -> CodeGraphIndex:
        files = self._collect_code_files()
        nodes: List[CodeNode] = []
        backends: Set[str] = set()

        for abs_path in files:
            rel_path = os.path.relpath(abs_path, self.project_path).replace("\\", "/")
            language = detect_language(abs_path)
            if not language:
                continue
            try:
                with open(abs_path, "r", encoding="utf-8") as fp:
                    source = fp.read()
            except (OSError, UnicodeDecodeError):
                continue

            raw_symbols, backend = extract_symbols(abs_path, source, language)
            backends.add(backend)
            for raw in raw_symbols:
                sig_hash = hashlib.sha1((raw.signature_text or raw.name).encode()).hexdigest()[:12]
                node = CodeNode(
                    node_id=make_node_id(rel_path, raw.qualified_name, raw.start_line),
                    language=language,
                    file_path=rel_path,
                    qualified_name=raw.qualified_name,
                    kind=raw.kind,
                    start_line=raw.start_line,
                    end_line=raw.end_line,
                    start_byte=raw.start_byte,
                    end_byte=raw.end_byte,
                    signature_hash=sig_hash,
                    body_hash=body_hash(raw.body_text),
                    summary=(raw.signature_text or raw.qualified_name)[:120],
                    calls=list(raw.call_names or []),
                    parent_class=raw.parent_class,
                )
                nodes.append(node)

        edges = self._resolve_edges(nodes)
        self._apply_called_by(nodes, edges)

        index = CodeGraphIndex(
            project_path=self.project_path,
            project_name=self.store.project_name,
            parser_backend="+".join(sorted(backends)) or "none",
            nodes=nodes,
            edges=edges,
        )
        index.rebuild_lookup_indexes()
        self.store.save(index)
        return index

    def _collect_code_files(self) -> List[str]:
        results: List[str] = []
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith(".")]
            for filename in files:
                _, ext = os.path.splitext(filename.lower())
                if ext not in CODE_EXTENSIONS:
                    continue
                results.append(os.path.join(root, filename))
        results.sort()
        return results

    def _resolve_edges(self, nodes: List[CodeNode]) -> List[CodeEdge]:
        by_file: Dict[str, List[CodeNode]] = {}
        by_basename: Dict[str, List[CodeNode]] = {}
        by_qn: Dict[str, List[CodeNode]] = {}

        for node in nodes:
            by_file.setdefault(node.file_path, []).append(node)
            base = node.qualified_name.split(".")[-1]
            by_basename.setdefault(base, []).append(node)
            by_qn.setdefault(node.qualified_name, []).append(node)

        edges: List[CodeEdge] = []
        for caller in nodes:
            for callee_name in caller.calls:
                targets, resolution = self._resolve_callee(
                    caller, callee_name, by_file, by_basename, by_qn
                )
                for target in targets:
                    edge_id = hashlib.sha1(
                        f"{caller.node_id}->{target.node_id}:{callee_name}".encode()
                    ).hexdigest()[:16]
                    edges.append(
                        CodeEdge(
                            edge_id=edge_id,
                            from_node_id=caller.node_id,
                            to_node_id=target.node_id,
                            callee_name=callee_name,
                            resolution=resolution,
                        )
                    )
        return edges

    def _resolve_callee(
        self,
        caller: CodeNode,
        callee_name: str,
        by_file: Dict[str, List[CodeNode]],
        by_basename: Dict[str, List[CodeNode]],
        by_qn: Dict[str, List[CodeNode]],
    ) -> Tuple[List[CodeNode], str]:
        if callee_name in by_qn:
            same_file = [n for n in by_qn[callee_name] if n.file_path == caller.file_path]
            if len(same_file) == 1:
                return same_file, "resolved"
            if len(by_qn[callee_name]) == 1:
                return by_qn[callee_name], "resolved"

        same_file_hits = [
            n for n in by_file.get(caller.file_path, [])
            if n.qualified_name == callee_name or n.qualified_name.endswith("." + callee_name)
        ]
        if len(same_file_hits) == 1:
            return same_file_hits, "resolved"
        if len(same_file_hits) > 1:
            return same_file_hits[:3], "ambiguous"

        global_hits = by_basename.get(callee_name, [])
        if len(global_hits) == 1:
            return global_hits, "resolved"
        if len(global_hits) > 1:
            return global_hits[:3], "ambiguous"

        init_hits = [
            n
            for file_nodes in by_file.values()
            for n in file_nodes
            if (
                n.qualified_name.endswith(f".{callee_name}.__init__")
                or n.qualified_name == f"{callee_name}.__init__"
            )
        ]
        if len(init_hits) == 1:
            return init_hits, "resolved"
        if len(init_hits) > 1:
            return init_hits[:3], "ambiguous"
        return [], "unresolved"

    def _apply_called_by(self, nodes: List[CodeNode], edges: List[CodeEdge]) -> None:
        node_map = {n.node_id: n for n in nodes}
        for node in nodes:
            node.calls = []
            node.called_by = []
        for edge in edges:
            if not edge.to_node_id:
                continue
            if edge.to_node_id in node_map:
                callee = node_map[edge.to_node_id]
                if edge.from_node_id not in callee.called_by:
                    callee.called_by.append(edge.from_node_id)
            if edge.from_node_id in node_map:
                caller = node_map[edge.from_node_id]
                if edge.to_node_id not in caller.calls:
                    caller.calls.append(edge.to_node_id)
