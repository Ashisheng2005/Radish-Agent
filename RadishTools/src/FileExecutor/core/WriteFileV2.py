from RadishTools.src.FileExecutor.core.write_v2.service import WriteFileV2Service
from RadishTools.src.FileExecutor.core.write_v2.raw_writer import RawWriteService

WriteFileV2_title = "writeFileV2Executor 写入工具"
WriteFileV2_docs = (
    "write_file_v2 工具用于按行差量修改文件（LLM first）。"
    "主协议仅支持 edits(JSON)，每项支持 op/start_line/end_line/new_text，兼容简写 op/s/e/t。"
    "支持 dry_run、return_patch、conflict_mode(strict|soft)、request_id。"
    "失败时返回稳定契约：error_code/retryable/suggested_action/diagnostics。"
)

WriteFileRaw_title = "writeFileRawExecutor - 纯内容写入工具"
WriteFileRaw_docs = (
    "write_file_raw 工具用于纯内容写入，模型输出什么就原样写入文件。"
    "参数：file_path(必填), content(必填, 字符串), encoding(可选, 默认utf-8)。"
    "文件不存在时自动创建目录和文件。"
)


def write_file_v2_execute(
    file_path,
    edits,
    encoding="utf-8",
    request_id=None,
    dry_run=False,
    return_patch=False,
    conflict_mode="strict",
):
    service = WriteFileV2Service()
    return service.execute(
        file_path=file_path,
        edits=edits,
        encoding=encoding,
        request_id=request_id,
        dry_run=dry_run,
        return_patch=return_patch,
        conflict_mode=conflict_mode,
    )


def write_file_raw_execute(
    file_path,
    content,
    encoding="utf-8",
):
    service = RawWriteService()
    return service.execute(
        file_path=file_path,
        content=content,
        encoding=encoding,
    )
