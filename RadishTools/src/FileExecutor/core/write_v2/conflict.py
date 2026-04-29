from typing import Dict, List, Sequence, Tuple

from .models import EditCommand


def _current_slice(lines: Sequence[str], edit: EditCommand) -> List[str]:
    if edit.op == "insert":
        return []
    if edit.end_line is None:
        return []
    return [x.rstrip("\r\n") for x in lines[edit.start_line - 1:edit.end_line]]


def detect_conflicts(lines: Sequence[str], edits: Sequence[EditCommand], conflict_mode: str) -> Tuple[bool, List[Dict[str, object]]]:
    diagnostics: List[Dict[str, object]] = []
    has_conflict = False

    for i, edit in enumerate(edits, start=1):
        if edit.expected_old_lines is None:
            continue
        current = _current_slice(lines, edit)
        expected = list(edit.expected_old_lines)
        if current != expected:
            has_conflict = True
            diagnostics.append(
                {
                    "index": i,
                    "reason": "expected_old_lines_mismatch",
                    "expected": expected,
                    "current": current,
                }
            )

    if has_conflict and conflict_mode == "strict":
        return False, diagnostics
    return True, diagnostics
