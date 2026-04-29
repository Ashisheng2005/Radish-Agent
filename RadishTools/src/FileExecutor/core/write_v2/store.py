import os
import tempfile
from typing import List, Sequence


class FileStore:
    def __init__(self, file_path: str, encoding: str = "utf-8"):
        self.file_path = file_path
        self.encoding = encoding

    def validate_target(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"{self.file_path} does not exist")
        if not os.path.isfile(self.file_path):
            raise IsADirectoryError(f"{self.file_path} is not a file")

    def read_lines(self) -> List[str]:
        with open(self.file_path, "r", encoding=self.encoding) as f:
            return f.read().splitlines(keepends=True)

    @staticmethod
    def detect_newline(lines: Sequence[str]) -> str:
        for line in lines:
            if line.endswith("\r\n"):
                return "\r\n"
            if line.endswith("\n"):
                return "\n"
        return "\n"

    def atomic_write(self, lines: Sequence[str]):
        directory = os.path.dirname(os.path.abspath(self.file_path)) or "."
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
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
