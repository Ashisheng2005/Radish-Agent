"""SymbolReadGate — session-scoped read-before-write enforcement with ASRG extensions."""

from __future__ import annotations

from threading import Lock
from typing import Dict, Optional, Set, Tuple


class SymbolReadGate:
    """Process-local gate; Polling sessions bind via gate_id."""

    _instances: Dict[str, "SymbolReadGate"] = {}
    _lock = Lock()

    def __init__(self, gate_id: str = "default"):
        self.gate_id = gate_id
        self._read_nodes: Set[str] = set()
        self._read_neighbors: Dict[str, Set[str]] = {}
        self._skipped_neighbors: Dict[str, str] = {}  # node_id -> justification

    @classmethod
    def get(cls, gate_id: str = "default") -> "SymbolReadGate":
        with cls._lock:
            if gate_id not in cls._instances:
                cls._instances[gate_id] = SymbolReadGate(gate_id)
            return cls._instances[gate_id]

    @classmethod
    def set_active_gate_id(cls, gate_id: Optional[str]) -> None:
        cls._active_gate_id = gate_id or "default"

    @classmethod
    def active(cls) -> "SymbolReadGate":
        gate_id = getattr(cls, "_active_gate_id", "default")
        return cls.get(gate_id)

    def record_read(self, node_id: str, neighbor_ids: Optional[Set[str]] = None) -> None:
        self._read_nodes.add(node_id)
        if neighbor_ids:
            nb = set(neighbor_ids)
            self._read_neighbors[node_id] = nb
            self._read_nodes.update(nb)

    def record_skip(self, node_ids: Set[str], justification: str) -> None:
        for nid in node_ids:
            if nid:
                self._skipped_neighbors[nid] = justification

    def is_read(self, node_id: str) -> bool:
        return node_id in self._read_nodes

    def is_skipped(self, node_id: str) -> bool:
        return node_id in self._skipped_neighbors

    def required_for_write(
        self,
        target_id: str,
        upstream_ids: Set[str],
        downstream_ids: Set[str],
    ) -> Set[str]:
        required = {target_id} | upstream_ids | downstream_ids
        return {
            nid for nid in required
            if nid and not self.is_read(nid) and not self.is_skipped(nid)
        }

    def clear(self) -> None:
        self._read_nodes.clear()
        self._read_neighbors.clear()
        self._skipped_neighbors.clear()


def neighbor_intent_summary(
    upstream_names: list,
    downstream_names: list,
) -> str:
    """Zero-cost neighbor intent string for read_symbol responses."""
    parts = []
    if upstream_names:
        parts.append(f"called_by: [{', '.join(upstream_names[:5])}]")
    if downstream_names:
        parts.append(f"calls: [{', '.join(downstream_names[:5])}]")
    return "; ".join(parts) if parts else "no direct neighbors"
