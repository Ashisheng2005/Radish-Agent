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

from code_graph.symbol_tools import (
    configure_graph,
    grep_code as _grep_code,
    grep_code_batch as _grep_code_batch,
    list_module_importers as _list_module_importers,
    list_symbol_callers as _list_symbol_callers,
    read_symbol as _read_symbol,
    search_symbols as _search_symbols,
    write_symbol as _write_symbol,
)

_read_allowlist_extra: set = set()


def sync_read_file_allowlist(names):
    """由 Polling/console 同步允许读取的敏感文件名（如 config.yaml 审计）。"""
    global _read_allowlist_extra
    _read_allowlist_extra = {str(x).strip().lower() for x in names if str(x).strip()}


def add_read_file_allowlist(*names):
    global _read_allowlist_extra
    for name in names:
        n = str(name).strip().lower()
        if n:
            _read_allowlist_extra.add(n)


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
    } | _read_allowlist_extra
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

def search_symbols(query, language=None, file_glob=None, limit=10):
    print(f"llm search_symbols: {query}")
    return _search_symbols(query=query, language=language, file_glob=file_glob, limit=limit)


def read_symbol(
    file_path,
    symbol,
    include_neighbors=True,
    upstream_depth=1,
    downstream_depth=1,
    include_source=True,
):
    print(f"llm read_symbol: {file_path} :: {symbol}")
    return _read_symbol(
        file_path=file_path,
        symbol=symbol,
        include_neighbors=include_neighbors,
        upstream_depth=upstream_depth,
        downstream_depth=downstream_depth,
        include_source=include_source,
    )


def write_symbol(
    file_path,
    symbol,
    edits,
    base_body_hash=None,
    dry_run=False,
    return_patch=True,
    conflict_mode="strict",
    encoding="utf-8",
):
    print(f"llm write_symbol: {file_path} :: {symbol}")
    return _write_symbol(
        file_path=file_path,
        symbol=symbol,
        edits=edits,
        base_body_hash=base_body_hash,
        dry_run=dry_run,
        return_patch=return_patch,
        conflict_mode=conflict_mode,
        encoding=encoding,
    )


def list_symbol_callers(file_path, symbol, limit=20, include_source=False):
    print(f"llm list_symbol_callers: {file_path} :: {symbol}")
    return _list_symbol_callers(
        file_path=file_path,
        symbol=symbol,
        limit=limit,
        include_source=include_source,
    )


def grep_code(
    pattern="",
    path_glob="**/*.py",
    max_hits=40,
    max_hits_per_file=3,
    case_insensitive=True,
    preset=None,
):
    print(f"llm grep_code: {pattern or preset}")
    return _grep_code(
        pattern=pattern,
        path_glob=path_glob,
        max_hits=max_hits,
        max_hits_per_file=max_hits_per_file,
        case_insensitive=case_insensitive,
        preset=preset,
    )


def grep_code_batch(
    patterns=None,
    path_glob="**/*.py",
    max_hits_total=40,
    max_hits_per_file=3,
    case_insensitive=True,
    preset=None,
):
    print(f"llm grep_code_batch: {preset or patterns}")
    return _grep_code_batch(
        patterns=patterns,
        path_glob=path_glob,
        max_hits_total=max_hits_total,
        max_hits_per_file=max_hits_per_file,
        case_insensitive=case_insensitive,
        preset=preset,
    )


def list_module_importers(module_file="yamlConfig.py", path_glob="**/*.py", limit=20):
    print(f"llm list_module_importers: {module_file}")
    return _list_module_importers(
        module_file=module_file,
        path_glob=path_glob,
        limit=limit,
    )


def init_code_graph(project_path=None, graph_path=""):
    """初始化代码图上下文；由 console/Polling 在启动时绑定当前工作目录 (cwd)。"""
    configure_graph(project_path=project_path, graph_path=graph_path)


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
    'create_path_or_file': createPathOrFile_docs,
    'search_symbols': "search_symbols(query, language=None, file_glob='*yaml*', limit=10) 检索符号；count=0 时看 hints/next_steps。",
    'read_symbol': "read_symbol(file_path, symbol, include_neighbors=True) 读取符号及上下游，记录门禁。",
    'write_symbol': "write_symbol(file_path, symbol, edits) 符号内相对行号 edits，需先 read_symbol。",
    'list_symbol_callers': "list_symbol_callers(file_path, symbol) 列出调用方；实例化请查 __init__ 或 list_module_importers。",
    'grep_code': "grep_code(pattern, path_glob='**/*.py') 文本搜索；preset='find_config_loader' 一次查 config/import/Config(。",
    'grep_code_batch': "grep_code_batch(patterns=[...]) 或 preset='find_config_loader'，一次多 pattern。",
    'list_module_importers': "list_module_importers(module_file='yamlConfig.py') 列出 import 该模块的文件。",
}

tools_title = {
    'tool_docs': "tool_docs:获取工具使用文档的工具, 用法参考: <tools>tool_docs('cmd,list_dir')</tools>，可以一次返回多个工具的使用文档，参数是工具名称列表的字符串格式，字符串内的工具名称用英文逗号分隔",
    'cmd': cmd_title,
    'list_dir': ListDir_title,
    'read_file': ReadFile_title,
    'write_file': WriteFileV2_title,
    'raw_write_file': WriteFileRaw_title,
    'create_path_or_file': createPathOrFile_title,
    'search_symbols': "search_symbols: 在项目代码图中检索函数/方法符号",
    'read_symbol': "read_symbol: 读取符号及上下游节点（修改前必读）",
    'write_symbol': "write_symbol: 按符号+相对行号 edits 写入（需先 read_symbol）",
    'list_symbol_callers': "list_symbol_callers: 列出符号的调用方（上游）",
    'grep_code': "grep_code: 在项目内按正则搜索源码文本",
    'grep_code_batch': "grep_code_batch: 一次执行多个 grep pattern",
    'list_module_importers': "list_module_importers: 列出 import 指定模块的文件",
}

tools_func = {
    'cmd': cmd,
    'list_dir': list_dir,
    'read_file': read_file,
    'write_file': write_file_v2,
    'raw_write_file': raw_write_file,
    'create_path_or_file': create_path_or_file,
    'tool_docs': tool_docs,
    'search_symbols': search_symbols,
    'read_symbol': read_symbol,
    'write_symbol': write_symbol,
    'list_symbol_callers': list_symbol_callers,
    'grep_code': grep_code,
    'grep_code_batch': grep_code_batch,
    'list_module_importers': list_module_importers,
}


if __name__ == '__main__':
    output = tool_docs('cmd,list_dir,read_file,write_file,create_path_or_file')
    print(output)

# 生成 OpenAI tools JSON 格式
tools_json = [
    {
        "type": "function",
        "function": {
            "name": "tool_docs",
            "description": "获取一个或多个工具的使用文档，参数为工具名称，多个用英文逗号分隔",
            "parameters": {
                "type": "object",
                "properties": {
                    "tools_name": {
                        "type": "string",
                        "description": "工具名称，多个用英文逗号分隔，例如 'cmd,list_dir'"
                    }
                },
                "required": ["tools_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cmd",
            "description": "在系统 shell 中执行命令。首次尝试指定编码，失败时自动回退到多种编码重试",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "命令输出的编码（如 utf-8、gbk），不指定则自动检测"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "递归列出指定路径下的文件和目录树",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定文件的内容，支持通过行号范围读取部分内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要读取的文件路径"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从1开始），不指定则从文件头开始"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（包含），不指定则读到文件尾"
                    },
                    "line_number": {
                        "type": "boolean",
                        "description": "是否在每行前显示行号，默认 false"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "对文件进行增量编辑（edits JSON），支持 dry_run、return_patch 和冲突模式。优先使用 edits 协议",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要修改的文件路径"
                    },
                    "edits": {
                        "type": "string",
                        "description": "编辑操作的 JSON 字符串，格式: [{\"op\":\"insert|delete|replace\",\"s\":行号,\"t\":\"内容\"}]"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8"
                    },
                    "request_id": {
                        "type": "string",
                        "description": "可选的请求追踪 ID"
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "为 true 时仅预览变更，不实际写入"
                    },
                    "return_patch": {
                        "type": "boolean",
                        "description": "为 true 时返回 diff patch"
                    },
                    "conflict_mode": {
                        "type": "string",
                        "enum": ["strict", "soft"],
                        "description": "冲突处理模式：strict 严格模式，soft 宽松模式"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "raw_write_file",
            "description": "将原始内容写入文件，会覆盖已有内容。自动创建父目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要写入的文件路径"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的完整内容"
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8"
                    }
                },
                "required": ["file_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbols",
            "description": "在项目代码图 CODE_GRAPH 中检索函数/方法符号；无结果时返回 hints 与 next_steps，勿重复换词搜索超过 2 次",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "符号名或关键词"},
                    "language": {"type": "string", "description": "可选语言过滤: python/javascript/java/csharp/cpp"},
                    "file_glob": {"type": "string", "description": "可选文件路径正则过滤"},
                    "limit": {"type": "integer", "description": "返回条数上限，默认 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_symbol",
            "description": "读取目标符号源码及一阶上下游节点；修改代码前必须先调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径（相对项目根或绝对路径）"},
                    "symbol": {"type": "string", "description": "符号名或 qualified_name"},
                    "include_neighbors": {"type": "boolean", "description": "是否包含上下游，默认 true"},
                    "upstream_depth": {"type": "integer", "description": "上游深度，默认 1"},
                    "downstream_depth": {"type": "integer", "description": "下游深度，默认 1"},
                },
                "required": ["file_path", "symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_symbol_callers",
            "description": "列出代码图中调用目标符号的上游（called_by）。查类是否被用：优先 list_module_importers 或 Config.__init__ 的 callers，勿仅凭 .get 无结果判死代码",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "定义符号的文件路径"},
                    "symbol": {"type": "string", "description": "符号名或 qualified_name"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 20"},
                    "include_source": {"type": "boolean", "description": "是否包含调用方源码"},
                },
                "required": ["file_path", "symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_code",
            "description": "项目内正则搜索；调查 config 时用 path_glob='**/*.py'；preset='find_config_loader' 一次查字面量/import/Config(；有 per-file 上限与 truncated 说明",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则，如 config\\.yaml；preset 时可省略"},
                    "path_glob": {"type": "string", "description": "路径过滤，建议 **/*.py"},
                    "max_hits": {"type": "integer", "description": "总命中上限，默认 40"},
                    "max_hits_per_file": {"type": "integer", "description": "单文件命中上限，默认 3"},
                    "case_insensitive": {"type": "boolean", "description": "忽略大小写，默认 true"},
                    "preset": {"type": "string", "description": "find_config_loader：一次查 config.yaml / import yamlConfig / Config("},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_code_batch",
            "description": "一次执行多个 pattern 并合并去重；调查 config 推荐 preset='find_config_loader'，替代连续多次 grep_code",
            "parameters": {
                "type": "object",
                "properties": {
                    "patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "正则列表；使用 preset 时可省略",
                    },
                    "path_glob": {"type": "string", "description": "默认 **/*.py"},
                    "max_hits_total": {"type": "integer", "description": "总命中上限，默认 40"},
                    "max_hits_per_file": {"type": "integer", "description": "单文件上限，默认 3"},
                    "case_insensitive": {"type": "boolean", "description": "默认 true"},
                    "preset": {"type": "string", "description": "find_config_loader"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_module_importers",
            "description": "列出 import/引用指定模块（如 yamlConfig.py）的文件，用于判断模块是否被使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_file": {"type": "string", "description": "模块文件名，默认 yamlConfig.py"},
                    "path_glob": {"type": "string", "description": "默认 **/*.py"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 20"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_symbol",
            "description": "按符号内相对行号 edits 写入；必须先 read_symbol；图缺失时降级 write_file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "symbol": {"type": "string", "description": "符号名或 qualified_name"},
                    "edits": {
                        "type": "string",
                        "description": "函数内相对行号 edits JSON，如 [{\"op\":\"replace\",\"s\":2,\"e\":3,\"t\":\"...\"}]",
                    },
                    "base_body_hash": {"type": "string", "description": "可选，read_symbol 返回的 body_hash 用于冲突检测"},
                    "dry_run": {"type": "boolean", "description": "仅预览"},
                    "return_patch": {"type": "boolean", "description": "返回 patch"},
                    "conflict_mode": {"type": "string", "enum": ["strict", "soft"]},
                },
                "required": ["file_path", "symbol", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_path_or_file",
            "description": "在指定路径创建文件或目录。创建文件需设置 is_file=True",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要创建的路径"
                    },
                    "is_file": {
                        "type": "boolean",
                        "description": "True 创建文件，False（默认）创建目录"
                    }
                },
                "required": ["path"]
            }
        }
    }
]
