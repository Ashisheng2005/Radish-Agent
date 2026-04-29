from typing import Dict


ERROR_DEFINITIONS: Dict[str, Dict[str, object]] = {
    "path_not_found": {
        "retryable": False,
        "suggested_action": "check_file_path",
    },
    "not_a_file": {
        "retryable": False,
        "suggested_action": "provide_file_path",
    },
    "invalid_arguments": {
        "retryable": True,
        "suggested_action": "fix_arguments_then_retry",
    },
    "range_out_of_bounds": {
        "retryable": True,
        "suggested_action": "read_file_then_retry",
    },
    "conflict_detected": {
        "retryable": True,
        "suggested_action": "read_file_then_retry",
    },
    "io_error": {
        "retryable": True,
        "suggested_action": "retry_or_check_permissions",
    },
}


def classify_exception(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "path_not_found"
    if isinstance(exc, IsADirectoryError):
        return "not_a_file"

    message = str(exc)
    lowered = message.lower()
    if "冲突检测失败" in message:
        return "conflict_detected"
    if "超出文件总行数" in message or "超出可插入范围" in message or "行号必须" in message:
        return "range_out_of_bounds"
    if "json" in lowered or "缺少" in message or "必须提供" in message or "不应提供" in message:
        return "invalid_arguments"
    if "op=" in message and "不支持" in message:
        return "invalid_arguments"
    if isinstance(exc, (OSError, UnicodeError)):
        return "io_error"
    return "io_error"


def make_error_payload(error_code: str, message: str):
    detail = ERROR_DEFINITIONS.get(error_code, ERROR_DEFINITIONS["io_error"])
    return {
        "error_code": error_code,
        "error": message,
        "retryable": bool(detail["retryable"]),
        "suggested_action": str(detail["suggested_action"]),
    }
