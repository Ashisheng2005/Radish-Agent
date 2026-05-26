"""Graph-based retrieval engine for project-level code graphs."""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .models import CodeGraphIndex, CodeNode


class CodeGraphRetriever:
    """Project-level graph retrieval with call-chain and neighbor propagation."""

    def __init__(self, index: CodeGraphIndex):
        self.index = index
        self.node_map = {n.node_id: n for n in index.nodes}

    def match_by_name(self, query: str, limit: int = 10) -> List[CodeNode]:
        q = query.strip().lower()
        terms = [t for t in q.replace("(", " ").replace(")", " ").split() if t]
        scored: List[Tuple[int, CodeNode]] = []
        for node in self.index.nodes:
            score = 0
            qn = node.qualified_name.lower()
            base = qn.split(".")[-1]
            for t in terms:
                if t == base:
                    score += 3
                elif t in qn:
                    score += 1
            if score:
                scored.append((score, node))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [n for _, n in scored[:limit]]

    def callers(self, target_id: str, depth: int = 2) -> Set[str]:
        result: Set[str] = set()
        q = deque([(target_id, 0)])
        visited = {target_id}
        while q:
            nid, d = q.popleft()
            node = self.node_map.get(nid)
            if not node:
                continue
            for cid in node.called_by:
                if cid in visited:
                    continue
                visited.add(cid)
                result.add(cid)
                if d + 1 < depth:
                    q.append((cid, d + 1))
        return result

    def recall_candidates(self, query: str, depth: int = 2, max_candidates: int = 50) -> List[CodeNode]:
        """Stage 1 graph recall for G2R."""
        seeds = self.match_by_name(query, limit=5)
        cand_ids: Set[str] = set()
        for seed in seeds:
            cand_ids.add(seed.node_id)
            cand_ids.update(self.callers(seed.node_id, depth=depth))
            for cid in seed.calls:
                if cid in self.node_map:
                    cand_ids.add(cid)
        nodes = [self.node_map[nid] for nid in cand_ids if nid in self.node_map]
        return nodes[:max_candidates]

    def score_nodes(self, query: str, nodes: List[CodeNode]) -> List[Tuple[CodeNode, float]]:
        q = query.lower()
        ranked: List[Tuple[CodeNode, float]] = []
        for node in nodes:
            score = 0.0
            if node.qualified_name.lower() in q or node.qualified_name.split(".")[-1].lower() in q:
                score += 2.0
            score += min(len(node.called_by), 10) * 0.05
            ranked.append((node, score))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked
