"""符号读取门禁：记录会话内已读节点，供 write_symbol 校验。"""

from __future__ import annotations

from threading import Lock
from typing import Dict, Optional, Set


class SymbolReadGate:
    """进程内单例门禁；Polling 会话可绑定 gate_id 隔离。"""

    _instances: Dict[str, "SymbolReadGate"] = {}
    _lock = Lock()

    def __init__(self, gate_id: str = "default"):
        self.gate_id = gate_id
        self._read_nodes: Set[str] = set()
        self._read_neighbors: Dict[str, Set[str]] = {}

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

    def is_read(self, node_id: str) -> bool:
        return node_id in self._read_nodes

    def required_for_write(self, target_id: str, upstream_ids: Set[str], downstream_ids: Set[str]) -> Set[str]:
        required = {target_id} | upstream_ids | downstream_ids
        return {nid for nid in required if nid and not self.is_read(nid)}

    def clear(self) -> None:
        self._read_nodes.clear()
        self._read_neighbors.clear()
