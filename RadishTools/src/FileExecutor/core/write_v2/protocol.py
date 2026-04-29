import json
from typing import Any, List

from .models import EditCommand, WriteRequest


def parse_edits_payload(edits_payload: Any) -> List[EditCommand]:
    if isinstance(edits_payload, str):
        try:
            parsed = json.loads(edits_payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"edits JSON 解析失败: {exc}") from exc
    else:
        parsed = edits_payload

    if isinstance(parsed, dict):
        if "edits" not in parsed:
            raise ValueError("edits 对象缺少 'edits' 字段")
        parsed = parsed["edits"]

    if not isinstance(parsed, list):
        raise ValueError("edits 必须是 list 或包含 edits 字段的 dict")

    edits: List[EditCommand] = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"edit #{idx}: 每一项都必须是对象")

        op = item.get("op", "replace")
        start_line = item.get("start_line", item.get("s"))
        end_line = item.get("end_line", item.get("e"))
        has_new_text = "new_text" in item or "t" in item
        new_text = item.get("new_text", item.get("t"))
        expected_old_lines = item.get("expected_old_lines")

        if start_line is None:
            raise ValueError(f"edit #{idx}: 缺少 start_line")
        if op not in {"insert", "replace", "delete"}:
            raise ValueError(f"edit #{idx}: 不支持的 op={op}")
        if not isinstance(start_line, int):
            raise ValueError(f"edit #{idx}: start_line 必须是 int")
        if end_line is not None and not isinstance(end_line, int):
            raise ValueError(f"edit #{idx}: end_line 必须是 int 或 null")
        if expected_old_lines is not None:
            if not isinstance(expected_old_lines, list) or not all(isinstance(x, str) for x in expected_old_lines):
                raise ValueError(f"edit #{idx}: expected_old_lines 必须是 string 列表或 null")

        if op == "delete":
            if has_new_text:
                raise ValueError(f"edit #{idx}: delete 不应提供 new_text")
            new_lines: List[str] = []
        else:
            if not has_new_text:
                raise ValueError(f"edit #{idx}: {op} 必须提供 new_text")
            if not isinstance(new_text, str):
                raise ValueError(f"edit #{idx}: new_text 必须是 string")
            new_lines = new_text.splitlines()

        edits.append(
            EditCommand(
                op=op,  # type: ignore[arg-type]
                start_line=start_line,
                end_line=end_line,
                new_lines=new_lines,
                expected_old_lines=expected_old_lines,
            )
        )
    return edits


def parse_write_request(
    file_path: str,
    edits_payload: Any,
    encoding: str = "utf-8",
    request_id: str = None,
    dry_run: bool = False,
    return_patch: bool = False,
    conflict_mode: str = "strict",
) -> WriteRequest:
    if not file_path:
        raise ValueError("file_path 不能为空")
    if edits_payload is None:
        raise ValueError("edits 不能为空")
    if conflict_mode not in {"strict", "soft"}:
        raise ValueError("conflict_mode 仅支持 strict 或 soft")

    edits = parse_edits_payload(edits_payload)
    if not edits:
        raise ValueError("edits 不能为空")

    return WriteRequest(
        file_path=file_path,
        edits=edits,
        encoding=encoding,
        request_id=request_id,
        dry_run=dry_run,
        return_patch=return_patch,
        conflict_mode=conflict_mode,  # type: ignore[arg-type]
    )
