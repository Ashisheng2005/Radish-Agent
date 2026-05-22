"""CODE_GRAPH.json 读写与路径解析。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import CodeGraphIndex


class CodeGraphStore:
    GRAPH_FILENAME = "CODE_GRAPH.json"

    def __init__(self, project_path: str, wiki_root: Optional[str] = None):
        self.project_path = os.path.abspath(project_path)
        self.project_name = os.path.basename(self.project_path.rstrip(os.sep)) or "project"
        if wiki_root:
            self.wiki_root = os.path.abspath(wiki_root)
        else:
            self.wiki_root = os.path.join(self.project_path, "wiki")
        self.graph_dir = os.path.join(self.wiki_root, self.project_name)
        self.graph_path = os.path.join(self.graph_dir, self.GRAPH_FILENAME)

    @classmethod
    def resolve_graph_path(
        cls,
        project_path: Optional[str] = None,
        configured_path: str = "",
    ) -> Optional[Path]:
        if configured_path:
            p = Path(configured_path)
            if p.exists():
                return p
        if project_path:
            store = cls(project_path)
            if os.path.isfile(store.graph_path):
                return Path(store.graph_path)
        root = Path(__file__).resolve().parents[2]
        wiki_root = root / "wiki"
        if not wiki_root.exists():
            return None
        name = Path(project_path or root).name
        candidate = wiki_root / name / cls.GRAPH_FILENAME
        if candidate.exists():
            return candidate
        folders = sorted([p for p in wiki_root.iterdir() if p.is_dir()])
        if folders:
            fallback = folders[0] / cls.GRAPH_FILENAME
            if fallback.exists():
                return fallback
        return None

    def save(self, index: CodeGraphIndex) -> str:
        os.makedirs(self.graph_dir, exist_ok=True)
        index.rebuild_lookup_indexes()
        with open(self.graph_path, "w", encoding="utf-8") as fp:
            json.dump(index.to_dict(), fp, ensure_ascii=False, indent=2)
        return self.graph_path

    def load(self) -> Optional[CodeGraphIndex]:
        if not os.path.isfile(self.graph_path):
            return None
        with open(self.graph_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return CodeGraphIndex.from_dict(data)

    @classmethod
    def load_from_path(cls, path: str) -> Optional[CodeGraphIndex]:
        if not path or not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as fp:
            return CodeGraphIndex.from_dict(json.load(fp))
