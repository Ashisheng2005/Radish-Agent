import difflib
from typing import List, Sequence

from .models import EditCommand


def ensure_line_ending(line: str, newline: str) -> str:
    if line.endswith("\r\n") or line.endswith("\n"):
        return line
    return f"{line}{newline}"


def apply_edits(lines: Sequence[str], edits: Sequence[EditCommand], newline: str) -> List[str]:
    updated = list(lines)
    sorted_edits = sorted(edits, key=lambda e: e.start_line, reverse=True)

    for edit in sorted_edits:
        start = edit.start_line - 1
        if edit.op == "insert":
            replacement = [ensure_line_ending(line, newline) for line in edit.new_lines]
            updated[start:start] = replacement
        elif edit.op == "delete":
            end = edit.end_line if edit.end_line is not None else edit.start_line
            updated[start:end] = []
        else:
            end = edit.end_line if edit.end_line is not None else edit.start_line
            replacement = [ensure_line_ending(line, newline) for line in edit.new_lines]
            updated[start:end] = replacement

    return updated


def build_patch(file_path: str, original_lines: Sequence[str], updated_lines: Sequence[str]) -> str:
    return "".join(
        difflib.unified_diff(
            list(original_lines),
            list(updated_lines),
            fromfile=f"{file_path} (before)",
            tofile=f"{file_path} (after)",
            lineterm="",
        )
    )
