import os
from typing import Dict, Any

from .store import FileStore
from .models import WriteResult


class RawWriteService:
    '''
    直接将内容写入指定文件路径，覆盖原有内容。
    适用于需要完全替换文件内容的场景，如日志文件、配置文件等。
     - file_path: 目标文件路径
     - content: 要写入的内容
     - encoding: 文件编码，默认为 utf-8
     - 返回值: 包含写入结果的字典，字段包括：
        - ok: 写入是否成功
        - file: 目标文件路径
        - applied: 实际写入的行数（成功时为1，失败时为0）
        - error_code: 错误代码（失败时提供）
        - error_message: 错误信息（失败时提供）
    '''

    def execute(self, file_path: str, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
        try:
            directory = os.path.dirname(os.path.abspath(file_path)) or "."
            if not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            lines = content.splitlines(keepends=True)
            if not lines:
                lines = [""]
            store = FileStore(file_path, encoding)
            store.atomic_write(lines)
            return WriteResult(ok=True, file=file_path, applied=1).to_dict()
        except Exception as exc:
            from .errors import classify_exception, make_error_payload
            code = classify_exception(exc)
            payload = make_error_payload(code, str(exc))
            return WriteResult(ok=False, file=file_path, applied=0, **payload).to_dict()
