"""代码图数据模型与 JSON 序列化。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def body_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def make_node_id(file_path: str, qualified_name: str, start_line: int) -> str:
    raw = f"{file_path}::{qualified_name}::{start_line}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class CodeNode:
    node_id: str
    language: str
    file_path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    start_byte: int = 0
    end_byte: int = 0
    signature_hash: str = ""
    body_hash: str = ""
    summary: str = ""
    calls: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)
    parent_class: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "language": self.language,
            "file_path": self.file_path,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "signature_hash": self.signature_hash,
            "body_hash": self.body_hash,
            "summary": self.summary,
            "calls": list(self.calls),
            "called_by": list(self.called_by),
            "parent_class": self.parent_class,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeNode":
        return cls(
            node_id=str(data["node_id"]),
            language=str(data.get("language", "")),
            file_path=str(data.get("file_path", "")),
            qualified_name=str(data.get("qualified_name", "")),
            kind=str(data.get("kind", "function")),
            start_line=int(data.get("start_line", 1)),
            end_line=int(data.get("end_line", 1)),
            start_byte=int(data.get("start_byte", 0)),
            end_byte=int(data.get("end_byte", 0)),
            signature_hash=str(data.get("signature_hash", "")),
            body_hash=str(data.get("body_hash", "")),
            summary=str(data.get("summary", "")),
            calls=list(data.get("calls", [])),
            called_by=list(data.get("called_by", [])),
            parent_class=data.get("parent_class"),
        )


@dataclass
class CodeEdge:
    edge_id: str
    from_node_id: str
    to_node_id: str
    callee_name: str
    resolution: str = "resolved"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "callee_name": self.callee_name,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeEdge":
        return cls(
            edge_id=str(data["edge_id"]),
            from_node_id=str(data["from_node_id"]),
            to_node_id=str(data.get("to_node_id", "")),
            callee_name=str(data.get("callee_name", "")),
            resolution=str(data.get("resolution", "resolved")),
        )


@dataclass
class CodeGraphIndex:
    version: str = "v1"
    project_path: str = ""
    project_name: str = ""
    generated_at: str = field(default_factory=_utc_now_iso)
    parser_backend: str = "fallback"
    nodes: List[CodeNode] = field(default_factory=list)
    edges: List[CodeEdge] = field(default_factory=list)
    by_file: Dict[str, List[str]] = field(default_factory=dict)
    by_qualified_name: Dict[str, List[str]] = field(default_factory=dict)
    by_basename: Dict[str, List[str]] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)

    def rebuild_lookup_indexes(self) -> None:
        self.by_file = {}
        self.by_qualified_name = {}
        self.by_basename = {}
        for node in self.nodes:
            self.by_file.setdefault(node.file_path, []).append(node.node_id)
            self.by_qualified_name.setdefault(node.qualified_name, []).append(node.node_id)
            base = node.qualified_name.split(".")[-1]
            self.by_basename.setdefault(base, []).append(node.node_id)
        self.stats = {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "file_count": len(self.by_file),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "project_path": self.project_path,
            "project_name": self.project_name,
            "generated_at": self.generated_at,
            "parser_backend": self.parser_backend,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "by_file": self.by_file,
            "by_qualified_name": self.by_qualified_name,
            "by_basename": self.by_basename,
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeGraphIndex":
        nodes = [CodeNode.from_dict(x) for x in data.get("nodes", [])]
        edges = [CodeEdge.from_dict(x) for x in data.get("edges", [])]
        idx = cls(
            version=str(data.get("version", "v1")),
            project_path=str(data.get("project_path", "")),
            project_name=str(data.get("project_name", "")),
            generated_at=str(data.get("generated_at", _utc_now_iso())),
            parser_backend=str(data.get("parser_backend", "fallback")),
            nodes=nodes,
            edges=edges,
            by_file=dict(data.get("by_file", {})),
            by_qualified_name=dict(data.get("by_qualified_name", {})),
            by_basename=dict(data.get("by_basename", {})),
            stats=dict(data.get("stats", {})),
        )
        if not idx.by_file:
            idx.rebuild_lookup_indexes()
        return idx

    def get_node(self, node_id: str) -> Optional[CodeNode]:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def find_nodes_by_symbol(self, file_path: str, symbol: str) -> List[CodeNode]:
        symbol = symbol.strip()
        norm_file = file_path.replace("\\", "/") if file_path else ""
        matches: List[CodeNode] = []
        for node in self.nodes:
            node_file = node.file_path.replace("\\", "/")
            if norm_file and node_file != norm_file and not node_file.endswith("/" + norm_file.split("/")[-1]):
                continue
            qn = node.qualified_name
            base = qn.split(".")[-1]
            if symbol in {qn, base}:
                matches.append(node)
        return matches
