from typing import Any, Dict

from .conflict import detect_conflicts
from .engine import apply_edits, build_patch
from .errors import classify_exception, make_error_payload
from .models import WriteResult
from .protocol import parse_write_request
from .store import FileStore
from .validator import validate_edits


class WriteFileV2Service:
    """状态化写入流程：precheck -> conflict -> apply -> commit。"""

    def execute(
        self,
        file_path: str,
        edits: Any,
        encoding: str = "utf-8",
        request_id: str = None,
        dry_run: bool = False,
        return_patch: bool = False,
        conflict_mode: str = "strict",
    ) -> Dict[str, Any]:
        

        try:
            request = parse_write_request(
                file_path=file_path,
                edits_payload=edits,
                encoding=encoding,
                request_id=request_id,
                dry_run=dry_run,
                return_patch=return_patch,
                conflict_mode=conflict_mode,
            )

            store = FileStore(request.file_path, request.encoding)
            store.validate_target()  # precheck
            original_lines = store.read_lines()  # precheck
            newline = store.detect_newline(original_lines)

            diagnostics = validate_edits(original_lines, request.edits)  # precheck
            conflict_ok, conflict_diagnostics = detect_conflicts(
                original_lines,
                request.edits,
                request.conflict_mode,
            )  # conflict
            if not conflict_ok:
                payload = make_error_payload("conflict_detected", "冲突检测失败，目标内容已变化")
                return WriteResult(
                    ok=False,
                    file=request.file_path,
                    applied=0,
                    request_id=request.request_id,
                    dry_run=request.dry_run,
                    diagnostics=diagnostics + conflict_diagnostics,
                    **payload,
                ).to_dict()

            updated_lines = apply_edits(original_lines, request.edits, newline)  # apply
            patch = build_patch(request.file_path, original_lines, updated_lines) if request.return_patch else None

            if not request.dry_run:
                store.atomic_write(updated_lines)  # commit

            return WriteResult(
                ok=True,
                file=request.file_path,
                applied=len(request.edits),
                request_id=request.request_id,
                dry_run=request.dry_run,
                patch=patch,
            ).to_dict()
        except Exception as exc:
            code = classify_exception(exc)
            payload = make_error_payload(code, str(exc))
            return WriteResult(
                ok=False,
                file=file_path or "",
                applied=0,
                request_id=request_id,
                dry_run=bool(dry_run),
                diagnostics=[],
                **payload,
            ).to_dict()
