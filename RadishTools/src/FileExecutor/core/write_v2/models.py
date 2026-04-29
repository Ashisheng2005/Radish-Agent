from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence


EditOp = Literal["insert", "replace", "delete"]
ConflictMode = Literal["strict", "soft"]


@dataclass
class EditCommand:
    op: EditOp
    start_line: int
    end_line: Optional[int] = None
    new_lines: Sequence[str] = field(default_factory=list)
    expected_old_lines: Optional[Sequence[str]] = None


@dataclass
class WriteRequest:
    file_path: str
    edits: List[EditCommand]
    encoding: str = "utf-8"
    request_id: Optional[str] = None
    dry_run: bool = False
    return_patch: bool = False
    conflict_mode: ConflictMode = "strict"


@dataclass
class WriteError:
    code: str
    message: str
    retryable: bool
    suggested_action: str
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class WriteResult:
    ok: bool
    file: str
    applied: int
    request_id: Optional[str] = None
    dry_run: bool = False
    patch: Optional[str] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    retryable: Optional[bool] = None
    suggested_action: Optional[str] = None
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "ok": self.ok,
            "file": self.file,
            "applied": self.applied,
            "request_id": self.request_id,
            "dry_run": self.dry_run,
        }
        if self.patch is not None:
            result["patch"] = self.patch
        if not self.ok:
            result["error_code"] = self.error_code
            result["error"] = self.error
            result["retryable"] = self.retryable
            result["suggested_action"] = self.suggested_action
            result["diagnostics"] = self.diagnostics
        return result
