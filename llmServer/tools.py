from pathlib import Path
import sys
import locale
import os
import fnmatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from RadishTools.src.CmdExecutor.core.executor import CMDExecutor, cmd_title, cmd_docs
from RadishTools.src.FileExecutor.core.ListDir import *
from RadishTools.src.FileExecutor.core.ReadFile import *
from RadishTools.src.FileExecutor.core.WriteFileV2 import *
from RadishTools.src.FileExecutor.core.CreatePathOrFile import *


def cmd(command, encoding=None):
    """执行命令，编码优先级：入参 > 环境变量 > 系统首选编码。"""
    print(f"llm execute cmd: {command}\n")


    candidates = []
    if encoding:
        candidates.append(str(encoding))
    env_encoding = os.getenv("RADISH_CMD_ENCODING", "").strip()
    if env_encoding:
        candidates.append(env_encoding)
    candidates.append(locale.getpreferredencoding(False) or "utf-8")
    candidates.extend(["utf-8", "gbk"])

    seen = set()
    ordered = []
    for item in candidates:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)

    last_error = None
    for enc in ordered:
        try:
            executor = CMDExecutor(work_dir='../', timeout=30, encoding=enc)
            executor.initialize(session_id="cmd_executor")
            result = executor.execute_command(command, wait=True)
            executor.close()
            return result.output.strip()
        except Exception as err:
            last_error = err
            continue

    return f"cmd执行失败: {last_error}"

def list_dir(path):
    print(f"llm list dir: {path}")

    executor = listDirExecutor(path=path)
    executor.build_tree()
    return executor.get_tree()

def read_file(file_path, start_line=None, end_line=None, line_number=False):
    print(f"llm read file: {file_path}, { {start_line, '-', end_line} if start_line else 'all'}")

    normalized = str(file_path).replace("\\", "/").lower()
    basename = normalized.split("/")[-1]
    deny_patterns = [".env", "config.yaml", "config.yml", "*.key", "*secret*", "credentials.json"]
    allowlist = {
        x.strip().lower()
        for x in os.getenv("RADISH_READFILE_ALLOWLIST", "").split(",")
        if x.strip()
    }
    if basename not in allowlist:
        for pat in deny_patterns:
            if fnmatch.fnmatch(basename, pat) or pat in normalized:
                return {
                    "ok": False,
                    "tool": "read_file",
                    "error_type": "sensitive_file_blocked",
                    "error": f"安全策略阻止读取敏感文件: {file_path}",
                }
    executor = readFileExecutor(file_path=file_path, start_line=start_line, end_line=end_line, line_number=line_number)
    return executor.execute()


def write_file_v2(
    file_path,
    edits=None,
    encoding="utf-8",
    request_id=None,
    dry_run=False,
    return_patch=False,
    conflict_mode="strict",
):
    # 显示具体修改了那个文件
    print(f"llm write {file_path}")

    return write_file_v2_execute(
        file_path=file_path,
        edits=edits,
        encoding=encoding,
        request_id=request_id,
        dry_run=dry_run,
        return_patch=return_patch,
        conflict_mode=conflict_mode,
    )

def raw_write_file(
    file_path,
    content,
    encoding="utf-8"
):
    print(f"llm raw write {file_path}")

    return write_file_raw_execute(
        file_path=file_path,
        content=content,
        encoding=encoding
    )


def create_path_or_file(path, is_file=False):
    print(f"llm create path or file: {path}, is_file: {is_file}")

    executor = createPathOrFileExecutor(path=path, is_file=is_file)
    return executor.execute()

def tool_docs(*tools_name):
    """
    支持以下输入形态：
    - tool_docs("read_file,list_dir")
    - tool_docs("read_file", "list_dir")
    - tool_docs(["read_file", "list_dir"])
    """
    raw_items = []
    for item in tools_name:
        if isinstance(item, (list, tuple, set)):
            raw_items.extend([str(x) for x in item])
        else:
            raw_items.append(str(item))

    merged = ",".join(raw_items)
    content = ''
    for name in merged.split(","):
        tool_name = name.strip()
        if not tool_name:
            continue
        if tool_name in tools_docs:
            content += f"{tool_name}: {tools_docs[tool_name]}\n"
            # print(f"{tool_name}: {tools_docs[tool_name]}")
        else:
            content += f"No documentation available for tool: {tool_name}\n"
            # print(f"No documentation available for tool: {tool_name}")
    # return tools_docs.get(tool_name, "No documentation available for this tool.")
    return content

tools_docs = {
    'tool_docs': "tool_docs工具可以获取工具使用文档，参数是工具名称列表的字符串格式，字符串内的工具名称用英文逗号分隔，例如：<tools>tool_docs('cmd,list_dir')</tools>，可以一次返回多个工具的使用文档",
    'cmd': cmd_docs,
    'list_dir': ListDir_docs,
    'read_file': ReadFile_docs,
    'write_file': WriteFileV2_docs,
    'raw_write_file': WriteFileRaw_docs,
    'create_path_or_file': createPathOrFile_docs
}

tools_title = {
    'tool_docs': "tool_docs:获取工具使用文档的工具, 用法参考: <tools>tool_docs('cmd,list_dir')</tools>，可以一次返回多个工具的使用文档，参数是工具名称列表的字符串格式，字符串内的工具名称用英文逗号分隔",
    'cmd': cmd_title,
    'list_dir': ListDir_title,
    'read_file': ReadFile_title,
    'write_file': WriteFileV2_title,
    'raw_write_file': WriteFileRaw_title,
    'create_path_or_file': createPathOrFile_title
}

tools_func = {
    'cmd': cmd,
    'list_dir': list_dir,
    'read_file': read_file,
    'write_file': write_file_v2,
    'raw_write_file': raw_write_file,
    'create_path_or_file': create_path_or_file,
    'tool_docs': tool_docs
}


if __name__ == '__main__':
    output = tool_docs('cmd,list_dir,read_file,write_file,create_path_or_file')
    print(output)