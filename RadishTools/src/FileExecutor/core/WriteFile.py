import os
import json
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Union
import json
import re
import tempfile
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

WriteFile_title = "writeFileExecutor - 写入文件工具"
WriteFile_docs = (
    "write_file工具用于按行差量修改文件，推荐主协议为 edits(JSON)。"
    "标准字段：op/start_line/end_line/new_text；紧凑字段：op/s/e/t（与标准字段等价）。"
    "op 仅支持 insert/delete/replace。"
    "高频正确示例："
    "<tools>write_file('./main.py', edits='[{\"op\":\"replace\",\"s\":3,\"e\":4,\"t\":\"for i in range(5):\\n    print(i)\"}]')</tools>。"
    "<tools>write_file('./main.py', edits='[{\"op\":\"insert\",\"s\":10,\"t\":\"print(\\\"tail\\\")\"}]')</tools>。"
    "<tools>write_file('./main.py', edits='[{\"op\":\"delete\",\"s\":8,\"e\":9}]')</tools>。"
    "code_chunk 仅作为兼容输入。常见错误：delete 传入 t、insert 传入 e、非JSON格式输入。"
    "若返回 invalid_arguments，请按 error/hint 修正参数后重试。"
)


def classify_write_error(exc: Exception):
    """将底层异常归一为稳定错误码，便于模型修参重试。"""
    if isinstance(exc, FileNotFoundError):
        return "path_not_found", "请确认 file_path 存在且可访问。"
    if isinstance(exc, IsADirectoryError):
        return "not_a_file", "file_path 必须是文件而不是目录。"

    message = str(exc)
    lowered = message.lower()

    if "冲突检测失败" in message:
        return "conflict_detected", "目标内容已变化，请先 read_file 后按最新内容重试。"
    if "超出文件总行数" in message or "超出可插入范围" in message or "行号必须" in message:
        return "range_out_of_bounds", "请检查 s/e 行号范围是否在文件可编辑区间。"
    if "json" in lowered or "缺少" in message or "必须提供" in message or "不应提供" in message:
        return "invalid_arguments", "请使用合法 edits(JSON)；delete 不带 t，insert 不带 e。"
    if "op=" in message and "不支持" in message:
        return "invalid_arguments", "op 仅支持 insert/delete/replace。"
    if isinstance(exc, (OSError, UnicodeError)):
        return "io_error", "文件读写失败，请检查编码、权限或文件占用。"

    return "io_error", "写入失败，请检查参数、权限和目标文件状态后重试。"

@dataclass
class LineEdit:
    """单个差量编辑。

    op 支持:
    - replace: 替换 [start_line, end_line] 闭区间，必须提供 new_lines。
    - insert: 在 start_line 前插入 new_lines；start_line 可为 total+1 表示末尾追加。
    - delete: 删除 [start_line, end_line] 闭区间，不需要 new_lines。

    所有行号均为 1-based。
    expected_old_lines 可选，用于冲突检测（乐观锁）。
    """

    start_line: int
    end_line: Optional[int] = None
    new_lines: Sequence[str] = ()
    op: str = "replace"
    expected_old_lines: Optional[Sequence[str]] = None

def parse_chunk_edits(code_chunk: str) -> List[LineEdit]:
    """把 chunk 字符串解析为 LineEdit 列表。

    支持格式:
    <chunk:insert-1-end>...</chunk>
    <chunk:replace-3-4>...</chunk>
    <chunk:delete-5-6></chunk>
    """
    if not isinstance(code_chunk, str) or not code_chunk.strip():
        raise ValueError("code_chunk 不能为空字符串")

    pattern = re.compile(
        r"<chunk:(insert|replace|delete)-(\d+)-(end|\d+)>(.*?)</chunk>",
        re.DOTALL,
    )

    edits: List[LineEdit] = []
    cursor = 0
    matched = False

    for idx, match in enumerate(pattern.finditer(code_chunk), start=1):
        matched = True
        # 只允许 chunk 之间存在空白字符，避免协议外文本混入。
        if code_chunk[cursor:match.start()].strip():
            raise ValueError(f"chunk #{idx}: 检测到无效文本，请仅输出 <chunk:...></chunk> 块")

        op = match.group(1)
        start_line = int(match.group(2))
        end_token = match.group(3)
        body = match.group(4)

        if op == "insert":
            if end_token != "end":
                raise ValueError(f"chunk #{idx}: insert 结尾标记必须为 end")
            if not body.strip():
                raise ValueError(f"chunk #{idx}: insert 需要提供内容")
            new_text = body.strip("\n")
            edits.append(LineEdit(op="insert", start_line=start_line, end_line=None, new_lines=new_text.splitlines()))

        elif op == "replace":
            if end_token == "end":
                raise ValueError(f"chunk #{idx}: replace 的 end_line 必须是数字")
            if not body.strip():
                raise ValueError(f"chunk #{idx}: replace 需要提供内容")
            end_line = int(end_token)
            new_text = body.strip("\n")
            edits.append(LineEdit(op="replace", start_line=start_line, end_line=end_line, new_lines=new_text.splitlines()))

        else:  # delete
            if end_token == "end":
                raise ValueError(f"chunk #{idx}: delete 的 end_line 必须是数字")
            if body.strip():
                raise ValueError(f"chunk #{idx}: delete 不应包含正文内容")
            end_line = int(end_token)
            edits.append(LineEdit(op="delete", start_line=start_line, end_line=end_line, new_lines=[]))

        cursor = match.end()

    if not matched:
        raise ValueError("未检测到有效 chunk，格式示例: <chunk:insert-1-end>...</chunk>")

    if code_chunk[cursor:].strip():
        raise ValueError("chunk 末尾包含无效文本，请仅输出 <chunk:...></chunk> 块")

    return edits




def parse_line_edits(edits_payload: Any) -> List[LineEdit]:
    """把模型返回的 JSON 转换为 LineEdit 列表。

    支持以下输入:
    - JSON 字符串
    - Python list[dict]
    - Python dict，且包含键 edits

    每个 edit 的代码内容仅支持:
    - new_text: 多行字符串

    说明:
    - insert/replace 必须提供 new_text
    - delete 不应提供 new_text
    """
    if isinstance(edits_payload, str):
        try:
            parsed = json.loads(edits_payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"edits JSON 解析失败: {exc}， 传入的参数为：{edits_payload}") from exc
    else:
        parsed = edits_payload

    if isinstance(parsed, dict):
        if "edits" not in parsed:
            raise ValueError("edits 对象缺少 'edits' 字段")
        parsed = parsed["edits"]

    if not isinstance(parsed, list):
        raise ValueError("edits 必须是 list 或包含 edits 字段的 dict")

    result: List[LineEdit] = []
    for idx, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"edit #{idx}: 每一项都必须是对象")

        # 兼容 compact 协议：s/e/t 分别映射到 start_line/end_line/new_text
        if "start_line" not in item and "s" in item:
            item["start_line"] = item["s"]
        if "end_line" not in item and "e" in item:
            item["end_line"] = item["e"]
        if "new_text" not in item and "t" in item:
            item["new_text"] = item["t"]

        if "start_line" not in item:
            raise ValueError(f"edit #{idx}: 缺少 start_line")

        op = item.get("op", "replace")
        start_line = item["start_line"]
        end_line = item.get("end_line")
        # if "new_lines" in item:
        #     raise ValueError(f"edit #{idx}: new_lines 输入已禁用，请改用 new_text")

        has_new_text = "new_text" in item
        new_text = item.get("new_text")
        if has_new_text and not isinstance(new_text, str):
            raise ValueError(f"edit #{idx}: new_text 必须是 string")

        if op in {"insert", "replace"}:
            if not has_new_text:
                raise ValueError(f"edit #{idx}: {op} 必须提供 new_text")
            # 统一转为按行列表，后续执行逻辑保持不变
            new_lines = new_text.splitlines()
        else:  # delete
            if has_new_text:
                raise ValueError(f"edit #{idx}: delete 不应提供 new_text")
            new_lines = []

        expected_old_lines = item.get("expected_old_lines")

        if not isinstance(start_line, int):
            raise ValueError(f"edit #{idx}: start_line 必须是 int")
        if end_line is not None and not isinstance(end_line, int):
            raise ValueError(f"edit #{idx}: end_line 必须是 int 或 null")
        if not isinstance(new_lines, list) or not all(isinstance(x, str) for x in new_lines):
            raise ValueError(f"edit #{idx}: new_text 解析失败，无法转换为行列表")
        if expected_old_lines is not None:
            if not isinstance(expected_old_lines, list) or not all(isinstance(x, str) for x in expected_old_lines):
                raise ValueError(f"edit #{idx}: expected_old_lines 必须是 string 列表或 null")

        result.append(
            LineEdit(
                op=op,
                start_line=start_line,
                end_line=end_line,
                new_lines=new_lines,
                expected_old_lines=expected_old_lines,
            )
        )

    return result


class writeFileExecutor:
    def __init__(
        self,
        file_path: str,
        edits: Sequence[LineEdit],
        encoding: str = 'utf-8',
        legacy_text_result: bool = False,
    ):
        self.file_path = file_path
        self.edits = list(edits)
        self.encoding = encoding
        self.legacy_text_result = legacy_text_result

        if not self.file_path:
            raise ValueError("file_path 不能为空")
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"{self.file_path} does not exist")
        if not os.path.isfile(self.file_path):
            raise IsADirectoryError(f"{self.file_path} is not a file")
        if not self.edits:
            raise ValueError("edits 不能为空")

    @classmethod
    def from_json(
        cls,
        file_path: str,
        edits_payload: Any,
        encoding: str = 'utf-8',
        legacy_text_result: bool = False,
    ):
        """从 JSON 字符串或对象构建执行器。"""
        edits = parse_line_edits(edits_payload)
        return cls(file_path=file_path, edits=edits, encoding=encoding, legacy_text_result=legacy_text_result)

    @classmethod
    def from_chunks(
        cls,
        file_path: str,
        code_chunk: str,
        encoding: str = 'utf-8',
        legacy_text_result: bool = False,
    ):
        """从 chunk 字符串构建执行器。"""
        edits = parse_chunk_edits(code_chunk)
        return cls(file_path=file_path, edits=edits, encoding=encoding, legacy_text_result=legacy_text_result)

    @classmethod
    def from_payload(
        cls,
        file_path: str,
        edits_payload: Any = None,
        code_chunk: Optional[str] = None,
        encoding: str = 'utf-8',
        legacy_text_result: bool = False,
    ):
        """统一入口：优先使用 code_chunk，其次使用 edits_payload。"""
        if code_chunk is not None:
            return cls.from_chunks(
                file_path=file_path,
                code_chunk=code_chunk,
                encoding=encoding,
                legacy_text_result=legacy_text_result,
            )
        return cls.from_json(
            file_path=file_path,
            edits_payload=edits_payload,
            encoding=encoding,
            legacy_text_result=legacy_text_result,
        )


    def execute(self):
        """按行范围应用编辑，并原子覆盖源文件。"""
        try:
            # 读取原文件内容，检测换行风格，验证编辑合法性，应用编辑，最后写回文件。
            original_lines = self._read_lines()
            # 保持原文件的换行风格，避免不必要的格式变更
            newline = self._detect_newline(original_lines)

            # 验证编辑的行号范围和冲突检测，确保不覆盖重要内容
            self._validate_edits(original_lines)
            # 应用编辑，得到更新后的完整内容列表
            updated_lines = self._apply_edits(original_lines, newline)

            # 原子方式写回文件，尽量避免写入中断导致文件损坏
            self._atomic_write(updated_lines)
            
            result = {
                "ok": True,
                "file": self.file_path,
                "applied": len(self.edits),
                "message": "write success",
            }
            if self.legacy_text_result:
                return f"Successfully patched {self.file_path} with {len(self.edits)} edit(s)"
            return result
        
        except Exception as e:
            error_type, hint = classify_write_error(e)
            result = {
                "ok": False,
                "file": self.file_path,
                "applied": 0,
                "error_type": error_type,
                "error": str(e),
                "hint": hint,
            }
            if self.legacy_text_result:
                return f"Error writing to file: {e}"
            return result

    def _read_lines(self) -> List[str]:
        # keepends=True 保留原文件换行信息，便于尽量保持风格一致
        with open(self.file_path, 'r', encoding=self.encoding) as f:
            return f.read().splitlines(keepends=True)

    def _detect_newline(self, lines: Sequence[str]) -> str:
        # 简单检测换行风格：默认 \n，若出现 \r\n 则优先使用 Windows 风格
        for line in lines:
            if line.endswith('\r\n'):
                return '\r\n'
            if line.endswith('\n'):
                return '\n'
        return '\n'

    def _validate_edits(self, lines: Sequence[str]):
        '''验证差量编辑的范围和冲突检测。'''
        total = len(lines)
        for i, edit in enumerate(self.edits, start=1):
            if edit.op not in {"replace", "insert", "delete"}:
                raise ValueError(f"edit #{i}: 不支持的 op={edit.op}")

            if edit.start_line < 1:
                raise ValueError(f"edit #{i}: 行号必须 >= 1")

            if edit.op == "insert":
                if edit.end_line is not None:
                    raise ValueError(f"edit #{i}: insert 不应设置 end_line")
                if not edit.new_lines:
                    raise ValueError(f"edit #{i}: insert 必须提供 new_lines")
                # 允许在 total+1 插入，表示文件末尾追加
                if edit.start_line > total + 1:
                    raise ValueError(f"edit #{i}: insert 行号超出可插入范围 1..{total + 1}")

            elif edit.op == "delete":
                if edit.end_line is None:
                    raise ValueError(f"edit #{i}: delete 必须提供 end_line")
                if edit.start_line > edit.end_line:
                    raise ValueError(f"edit #{i}: start_line 不能大于 end_line")
                if edit.end_line > total:
                    raise ValueError(f"edit #{i}: end_line 超出文件总行数 {total}")
                if edit.new_lines:
                    raise ValueError(f"edit #{i}: delete 不应提供 new_lines")

            else:  # replace
                if edit.end_line is None:
                    raise ValueError(f"edit #{i}: replace 必须提供 end_line")
                if edit.start_line > edit.end_line:
                    raise ValueError(f"edit #{i}: start_line 不能大于 end_line")
                if edit.end_line > total:
                    raise ValueError(f"edit #{i}: end_line 超出文件总行数 {total}")

            if edit.expected_old_lines is not None:
                current = self._current_slice_for_conflict(lines, edit)
                expected = list(edit.expected_old_lines)
                if current != expected:
                    raise ValueError(
                        f"edit #{i}: 冲突检测失败，目标区间内容与 expected_old_lines 不一致"
                    )

    def _current_slice_for_conflict(self, lines: Sequence[str], edit: LineEdit) -> List[str]:
        # insert 的目标区间是“插入点前后上下文”之外的零宽区间，因此只接受 expected_old_lines 为空。
        if edit.op == "insert":
            return []

        end_line = edit.end_line if edit.end_line is not None else edit.start_line
        return [x.rstrip('\r\n') for x in lines[edit.start_line - 1:end_line]]

    def _apply_edits(self, lines: Sequence[str], newline: str) -> List[str]:
        updated = list(lines)

        # 倒序应用可避免前一次改动影响后续 edit 的行号定位
        sorted_edits = sorted(self.edits, key=lambda e: e.start_line, reverse=True)

        for edit in sorted_edits:
            start = edit.start_line - 1

            if edit.op == "insert":
                replacement = [self._ensure_line_ending(line, newline) for line in edit.new_lines]
                updated[start:start] = replacement
            elif edit.op == "delete":
                end = edit.end_line if edit.end_line is not None else edit.start_line
                updated[start:end] = []
            else:  # replace
                end = edit.end_line if edit.end_line is not None else edit.start_line
                replacement = [self._ensure_line_ending(line, newline) for line in edit.new_lines]
                updated[start:end] = replacement

        return updated

    def _ensure_line_ending(self, line: str, newline: str) -> str:
        if line.endswith('\r\n') or line.endswith('\n'):
            return line
        return f"{line}{newline}"

    def _atomic_write(self, lines: Sequence[str]):
        # 先写临时文件，再 os.replace 覆盖，尽量避免写入中断导致原文件损坏
        directory = os.path.dirname(os.path.abspath(self.file_path)) or '.'
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding=self.encoding,
                dir=directory,
                delete=False,
            ) as temp_file:
                temp_file.writelines(lines)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = temp_file.name

            os.replace(temp_path, self.file_path)
        except Exception:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            raise


if __name__ == "__main__":
    # 不再使用json，直接使用字符串
    code_chunk = '''<chunk:insert-1-end>
for i in range():
    print(i)

</chunk>
<chunk:replace-3-4>
print()
print()
</chunk>
<chunk:delete-5-6></chunk>'''

    executor = writeFileExecutor.from_chunks(file_path="./test.txt", code_chunk=code_chunk)
    result = executor.execute()
    print(result)