"""符号与调用提取：Python AST、tree-sitter 查询、正则降级。"""

from __future__ import annotations

import ast
import hashlib
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .parser import RawSymbol

CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(|"
    r"(?:self|cls|this)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(|"
    r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\("
)

PY_DEF_RE = re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
PY_CLASS_RE = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
JS_FUNC_RE = re.compile(
    r"(?:function\s+([A-Za-z_$][\w$]*)|"
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>)|"
    r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{)"
)
JAVA_METHOD_RE = re.compile(
    r"(?:public|private|protected|static|\s)+[\w\<\>\[\],\s]+\s+(\w+)\s*\([^;{]*\)\s*\{"
)
CS_METHOD_RE = re.compile(
    r"(?:public|private|protected|internal|static|\s)+[\w\<\>\[\],\s]+\s+(\w+)\s*\([^;{]*\)\s*\{"
)
CPP_FUNC_RE = re.compile(
    r"(?:[\w:\<\>\*&\s,]+)\s+(\w+)\s*\([^;{]*\)\s*(?:const)?\s*\{"
)


def _line_range(lines: List[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _find_calls_in_text(text: str, local_names: Set[str]) -> List[str]:
    found: Set[str] = set()
    for m in CALL_RE.finditer(text):
        for g in m.groups():
            if g and g in local_names:
                found.add(g)
    return sorted(found)


def extract_python_ast(file_path: str, source: str) -> List[RawSymbol]:
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return extract_regex_fallback(file_path, source, "python")

    local_funcs: Dict[str, ast.AST] = {}
    symbols: List[RawSymbol] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.class_stack: List[str] = []

        def visit_ClassDef(self, node: ast.ClassDef):
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef):
            self._add_func(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self._add_func(node)

        def _add_func(self, node):
            name = node.name
            qn = f"{self.class_stack[-1]}.{name}" if self.class_stack else name
            local_funcs[name] = node
            start = node.lineno
            end = node.end_lineno or start
            body_lines = lines[start - 1 : end]
            body_text = "\n".join(body_lines)
            sig_line = lines[start - 1] if start <= len(lines) else ""
            call_names = _collect_ast_calls(node, set(local_funcs.keys()))
            symbols.append(
                RawSymbol(
                    name=name,
                    qualified_name=qn,
                    kind="method" if self.class_stack else "function",
                    start_line=start,
                    end_line=end,
                    parent_class=self.class_stack[-1] if self.class_stack else None,
                    body_text=body_text,
                    signature_text=sig_line.strip(),
                    call_names=call_names,
                )
            )
            self.generic_visit(node)

    Visitor().visit(tree)
    return symbols


def _collect_ast_calls(node: ast.AST, local_names: Set[str]) -> List[str]:
    calls: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                calls.add(child.func.id)
            elif isinstance(child.func, ast.Attribute) and child.func.attr in local_names:
                calls.add(child.func.attr)
    return sorted(calls)


def extract_regex_fallback(file_path: str, source: str, language: str) -> List[RawSymbol]:
    lines = source.splitlines()
    symbols: List[RawSymbol] = []

    if language == "python":
        return _extract_python_regex(file_path, source, lines)

    patterns = {
        "javascript": JS_FUNC_RE,
        "java": JAVA_METHOD_RE,
        "csharp": CS_METHOD_RE,
        "cpp": CPP_FUNC_RE,
    }
    pat = patterns.get(language)
    if not pat:
        return symbols

    for idx, line in enumerate(lines, start=1):
        m = pat.search(line)
        if not m:
            continue
        name = next((g for g in m.groups() if g), None)
        if not name or name in {"if", "for", "while", "switch", "catch"}:
            continue
        end = _guess_block_end(lines, idx)
        body_text = _line_range(lines, idx, end)
        local_names = {s.name for s in symbols} | {name}
        symbols.append(
            RawSymbol(
                name=name,
                qualified_name=name,
                kind="function",
                start_line=idx,
                end_line=end,
                body_text=body_text,
                signature_text=line.strip(),
                call_names=_find_calls_in_text(body_text, local_names),
            )
        )
    return symbols


def _extract_python_regex(file_path: str, source: str, lines: List[str]) -> List[RawSymbol]:
    del file_path, source
    symbols: List[RawSymbol] = []
    class_stack: List[Tuple[str, int]] = []

    for idx, line in enumerate(lines, start=1):
        cm = PY_CLASS_RE.match(line)
        if cm:
            class_stack.append((cm.group("name"), len(cm.group("indent"))))
            continue
        while class_stack and len(line) - len(line.lstrip()) <= class_stack[-1][1]:
            class_stack.pop()
        dm = PY_DEF_RE.match(line)
        if not dm:
            continue
        name = dm.group("name")
        parent = class_stack[-1][0] if class_stack else None
        qn = f"{parent}.{name}" if parent else name
        end = _guess_block_end(lines, idx)
        body_text = _line_range(lines, idx, end)
        local_names = {s.name for s in symbols} | {name}
        symbols.append(
            RawSymbol(
                name=name,
                qualified_name=qn,
                kind="method" if parent else "function",
                start_line=idx,
                end_line=end,
                parent_class=parent,
                body_text=body_text,
                signature_text=line.strip(),
                call_names=_find_calls_in_text(body_text, local_names),
            )
        )
    return symbols


def _guess_block_end(lines: List[str], start_line: int) -> int:
    if start_line > len(lines):
        return start_line
    base_indent = len(lines[start_line - 1]) - len(lines[start_line - 1].lstrip())
    if "{" in lines[start_line - 1]:
        depth = 0
        for i in range(start_line - 1, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if depth <= 0 and i >= start_line - 1:
                return i + 1
        return len(lines)
    for i in range(start_line, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            return i
    return len(lines)


def extract_from_tree_sitter(file_path: str, source: str, language: str, tree: Any) -> List[RawSymbol]:
    """最小 tree-sitter 遍历：按语言节点类型提取函数。"""
    lines = source.splitlines()
    root = tree.root_node
    symbols: List[RawSymbol] = []

    node_types_map = {
        "python": {"function_definition", "function_declaration"},
        "javascript": {"function_declaration", "method_definition", "arrow_function", "function"},
        "java": {"method_declaration", "constructor_declaration"},
        "csharp": {"method_declaration", "constructor_declaration", "local_function_statement"},
        "cpp": {"function_definition", "function_declarator"},
    }
    target_types = node_types_map.get(language, set())

    def walk(node, parent_class: Optional[str] = None):
        ntype = node.type
        if ntype in {"class_definition", "class_declaration", "class_specifier"}:
            name_node = node.child_by_field_name("name")
            cls_name = _node_text(source, name_node) if name_node else None
            for child in node.children:
                walk(child, cls_name)
            return
        if ntype in target_types:
            name_node = node.child_by_field_name("name")
            name = _node_text(source, name_node) if name_node else f"anon_{node.start_point[0]}"
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            body_text = _line_range(lines, start, end)
            qn = f"{parent_class}.{name}" if parent_class else name
            local_names = {s.name for s in symbols} | {name}
            symbols.append(
                RawSymbol(
                    name=name,
                    qualified_name=qn,
                    kind="method" if parent_class else "function",
                    start_line=start,
                    end_line=end,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                    parent_class=parent_class,
                    body_text=body_text,
                    signature_text=lines[start - 1].strip() if start <= len(lines) else "",
                    call_names=_find_calls_in_text(body_text, local_names),
                )
            )
            return
        for child in node.children:
            walk(child, parent_class)

    walk(root)
    return symbols


def _node_text(source: str, node) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte]
