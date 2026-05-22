"""语言检测与解析后端：优先 tree-sitter，降级 AST/正则。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


EXT_TO_LANGUAGE: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".java": "java",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
}

CODE_EXTENSIONS = set(EXT_TO_LANGUAGE.keys())


def detect_language(file_path: str) -> Optional[str]:
    _, ext = os.path.splitext(file_path.lower())
    return EXT_TO_LANGUAGE.get(ext)


@dataclass
class RawSymbol:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    start_byte: int = 0
    end_byte: int = 0
    parent_class: Optional[str] = None
    body_text: str = ""
    signature_text: str = ""
    call_names: List[str] = None

    def __post_init__(self):
        if self.call_names is None:
            self.call_names = []


class TreeSitterBackend:
    """可选 tree-sitter 后端；未安装时返回 None。"""

    _initialized = False
    _parsers: Dict[str, object] = {}

    @classmethod
    def available(cls) -> bool:
        try:
            from tree_sitter import Language, Parser  # noqa: F401
            return True
        except ImportError:
            return False

    @classmethod
    def _ensure_parsers(cls) -> bool:
        if cls._initialized:
            return bool(cls._parsers)
        cls._initialized = True
        if not cls.available():
            return False
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_python as tsp
            import tree_sitter_javascript as tsj
            import tree_sitter_java as tsjava
            import tree_sitter_cpp as tscpp
            import tree_sitter_c_sharp as tscs

            mapping = {
                "python": tsp.language(),
                "javascript": tsj.language(),
                "java": tsjava.language(),
                "cpp": tscpp.language(),
                "csharp": tscs.language(),
            }
            for lang, lib in mapping.items():
                cls._parsers[lang] = Parser(Language(lib))
            return True
        except Exception:
            cls._parsers.clear()
            return False

    @classmethod
    def parse_file(cls, language: str, source: bytes) -> Optional[object]:
        if not cls._ensure_parsers():
            return None
        parser = cls._parsers.get(language)
        if parser is None:
            return None
        try:
            return parser.parse(source)
        except Exception:
            return None


def extract_symbols(file_path: str, source: str, language: str) -> Tuple[List[RawSymbol], str]:
    """提取文件符号，返回 (symbols, backend_name)。"""
    if language == "python":
        from .extractors import extract_python_ast
        return extract_python_ast(file_path, source), "ast"
    tree = TreeSitterBackend.parse_file(language, source.encode("utf-8", errors="replace"))
    if tree is not None:
        from .extractors import extract_from_tree_sitter
        symbols = extract_from_tree_sitter(file_path, source, language, tree)
        if symbols:
            return symbols, "tree-sitter"
    from .extractors import extract_regex_fallback
    return extract_regex_fallback(file_path, source, language), "regex"
