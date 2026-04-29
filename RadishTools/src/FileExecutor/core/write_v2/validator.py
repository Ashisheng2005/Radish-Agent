from typing import List, Sequence

from .models import EditCommand


def validate_edits(lines: Sequence[str], edits: Sequence[EditCommand]):
    total = len(lines)
    diagnostics: List[dict] = []

    for i, edit in enumerate(edits, start=1):
        if edit.start_line < 1:
            raise ValueError(f"edit #{i}: 行号必须 >= 1")

        if edit.op == "insert":
            if edit.end_line is not None:
                raise ValueError(f"edit #{i}: insert 不应设置 end_line")
            if not edit.new_lines:
                raise ValueError(f"edit #{i}: insert 必须提供 new_lines")
            if edit.start_line > total + 1:
                raise ValueError(f"edit #{i}: insert 行号超出可插入范围 1..{total + 1}")
            continue

        if edit.end_line is None:
            raise ValueError(f"edit #{i}: {edit.op} 必须提供 end_line")
        if edit.start_line > edit.end_line:
            raise ValueError(f"edit #{i}: start_line 不能大于 end_line")
        if edit.end_line > total:
            raise ValueError(f"edit #{i}: end_line 超出文件总行数 {total}")

        if edit.op == "delete" and edit.new_lines:
            raise ValueError(f"edit #{i}: delete 不应提供 new_lines")

        diagnostics.append(
            {
                "index": i,
                "op": edit.op,
                "start_line": edit.start_line,
                "end_line": edit.end_line,
            }
        )

    return diagnostics
