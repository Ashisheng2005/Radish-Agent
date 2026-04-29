"""
llmPolling 中的部分函数迁移,保持中枢流程的干净整洁
"""

import re
import json
from pathlib import Path

def detect_intent_mode(prompt: str) -> str:
    text = (prompt or "").lower()

    agent_keywords = [
        "重写", "改写", "优化", "封装", "重构", "改造", "实现", "修复", "更新", "修改", "新增",
        "rewrite", "refactor", "implement", "fix", "patch", "update", "modify", "create",
        ".py", ".js", ".ts", ".md", "代码", "文件",
    ]
    plan_keywords = [
        "计划", "方案", "步骤", "流程", "路线", "评估", "先不要改", "先规划",
        "plan", "roadmap", "workflow", "steps", "design", "approach",
    ]

    if any(k in text for k in agent_keywords):
        return "agent"
    if any(k in text for k in plan_keywords):
        return "plan"
    return "ask"

def get_system_info():
    """获取系统环境信息，供模型参考。"""
    try:
        import platform
        system_info = f"{platform.system()} {platform.release()} ({platform.architecture()[0]})"
        return system_info
    except Exception as e:
        print(f"获取系统信息失败: {e}")
        return "Unknown System"
        
def normalize_language(language):
    """把环境变量里的 locale 值转换成模型更容易理解的自然语言描述。"""
    language_value = (language or "").strip()
    if not language_value:
        return "Chinese"

    language_lower = language_value.lower()
    if language_lower in {"zh", "zh_cn", "zh-cn", "zh_hans", "chinese", "中文"}:
        return "Chinese"
    if language_lower in {"en", "en_us", "en-gb", "english", "英语"}:
        return "English"

    return language_value

def parse_bool(value, default=False):
    """稳健解析布尔配置，避免 bool('false') 误判为 True。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default

def build_write_strategy_hint(prompt: str) -> str:
    text = str(prompt or "").lower()
    large_write_signals = [
        "sql", "脚本", "script", "insert", "10万", "100000", "100k", "mock", "模拟数据",
    ]
    if not any(k in text for k in large_write_signals):
        return ""
    return (
        "\n\nLarge-file writing strategy:\n"
        "- If generating a brand-new multi-line script, create file with create_path_or_file(path, is_file=True).\n"
        "- Prefer one-shot cmd heredoc to write full content.\n"
        "- Avoid repeated write_file retries for long text; use write_file only for small incremental patches."
    )

def parse_tool_calls(reply):
    """用正则提取 <tools>name(args)</tools> 片段，并解析出工具名和参数。"""
    tool_pattern = re.compile(
        r"<tools>\s*(?P<name>[a-zA-Z_][\w]*)\s*\((?P<args>.*?)\)\s*</tool[s]?>",
        re.DOTALL,
    )
    tool_calls = []

    for match in tool_pattern.finditer(reply):
        tool_calls.append(
            {
                "name": match.group("name"),
                "args": match.group("args").strip(),
                "raw": match.group(0),
            }
        )

    return tool_calls

def coerce_tool_arguments(tool_name: str, args: tuple, kwargs: dict):
    """兼容模型把参数整体封装成 JSON 字符串的调用风格。"""
    coerced_args = tuple(args)
    coerced_kwargs = dict(kwargs)

    # 兼容：tool('{"path":"...","type":"file"}')
    if len(coerced_args) == 1 and not coerced_kwargs and isinstance(coerced_args[0], str):
        raw = coerced_args[0].strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                coerced_args = tuple()
                coerced_kwargs = payload

    # 关键字别名归一化
    if "path" in coerced_kwargs and "file_path" not in coerced_kwargs and tool_name in {"read_file", "write_file"}:
        coerced_kwargs["file_path"] = coerced_kwargs.pop("path")

    if tool_name == "create_path_or_file":
        # 若只给出 path，且看起来是文件路径（存在扩展名），默认按文件创建，避免误创建目录。
        if len(coerced_args) == 1 and not coerced_kwargs:
            path_text = str(coerced_args[0])
            suffix = Path(path_text).suffix
            if suffix:
                coerced_kwargs["path"] = path_text
                coerced_kwargs["is_file"] = True
                coerced_args = tuple()
        if "type" in coerced_kwargs and "is_file" not in coerced_kwargs:
            coerced_kwargs["is_file"] = str(coerced_kwargs.pop("type")).strip().lower() == "file"
        return coerced_args, coerced_kwargs

    if tool_name == "write_file":
        # 兼容错误写法：write_file(path, 'op=insert', 's=1', 't=...')
        if len(coerced_args) >= 2 and isinstance(coerced_args[0], str):
            pseudo_pairs = {}
            all_pairs = True
            for item in coerced_args[1:]:
                if not isinstance(item, str) or "=" not in item:
                    all_pairs = False
                    break
                key, val = item.split("=", 1)
                pseudo_pairs[key.strip()] = val.strip()
            if all_pairs and pseudo_pairs and "edits" not in coerced_kwargs and "code_chunk" not in coerced_kwargs:
                start_line = 1
                if str(pseudo_pairs.get("s", "1")).isdigit():
                    start_line = int(str(pseudo_pairs.get("s", "1")))
                edit_obj = {
                    "op": pseudo_pairs.get("op", "insert"),
                    "s": start_line,
                    "t": pseudo_pairs.get("t", ""),
                }
                e_val = pseudo_pairs.get("e")
                if e_val and str(e_val).isdigit():
                    edit_obj["e"] = int(e_val)
                coerced_kwargs["file_path"] = coerced_args[0]
                coerced_kwargs["edits"] = json.dumps([edit_obj], ensure_ascii=False)
                coerced_args = tuple()

        if "content" in coerced_kwargs and "code_chunk" not in coerced_kwargs:
            coerced_kwargs["code_chunk"] = coerced_kwargs.pop("content")

        # 兼容 kwargs 紧凑写法：write_file(path='a.py', op='insert', s=1, t='...')
        if (
            "edits" not in coerced_kwargs
            and "code_chunk" not in coerced_kwargs
            and "op" in coerced_kwargs
        ):
            op_val = str(coerced_kwargs.get("op", "")).strip().lower()
            file_path_val = coerced_kwargs.get("file_path")
            if file_path_val is None and coerced_args and isinstance(coerced_args[0], str):
                file_path_val = coerced_args[0]

            if op_val in {"insert", "replace", "delete"} and file_path_val:
                raw_start = coerced_kwargs.get("s", coerced_kwargs.get("start_line", 1))
                start_line = int(raw_start) if str(raw_start).isdigit() else 1
                raw_end = coerced_kwargs.get("e", coerced_kwargs.get("end_line"))
                end_line = int(raw_end) if raw_end is not None and str(raw_end).isdigit() else None
                new_text = coerced_kwargs.get("t", coerced_kwargs.get("new_text", ""))

                edit = {"op": op_val, "s": start_line}
                if end_line is not None:
                    edit["e"] = end_line
                if op_val != "delete":
                    edit["t"] = str(new_text)

                coerced_kwargs = {
                    "file_path": str(file_path_val),
                    "edits": json.dumps([edit], ensure_ascii=False),
                }
                coerced_args = tuple()
        return coerced_args, coerced_kwargs

    return coerced_args, coerced_kwargs

def clean_text(value: str) -> str:
    """清洗工具文本：去控制字符、替换乱码占位、压缩空白。"""
    if not isinstance(value, str):
        value = str(value)
    text = value.replace("\r\n", "\n")
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
    text = text.replace("\ufffd", "?")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def is_effectively_empty_reply(value: str) -> bool:
    """判空时先去除不可见字符，避免被空白噪声误导。"""
    if value is None:
        return True
    text = str(value)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"\s+", "", text)
    return text == ""

def trim_result_text(text: str, max_chars: int) -> str:
    """长文本改为头尾摘要，避免大段回填挤占上下文。"""
    if len(text) <= max_chars:
        return text
    head_size = max(120, int(max_chars * 0.55))
    tail_size = max(80, max_chars - head_size - 40)
    head = text[:head_size]
    tail = text[-tail_size:] if tail_size > 0 else ""
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n... [TRUNCATED {omitted} chars] ...\n{tail}"

def enforce_three_section_format(text: str) -> str:
    """强制归一为 Conclusion/Evidence/NextStep 三段。"""
    line_items = [x.strip(" -*\t") for x in text.splitlines() if x.strip()]
    sections = {"Conclusion": "", "Evidence": "", "NextStep": ""}
    for line in line_items:
        m = re.match(r"(?i)^(Conclusion|Evidence|NextStep)\s*:\s*(.*)$", line)
        if m:
            sections[m.group(1).title()] = m.group(2).strip()

    if not sections["Conclusion"] and line_items:
        sections["Conclusion"] = line_items[0]
    if not sections["Evidence"]:
        evidence_lines = [x for x in line_items if x != sections["Conclusion"]][:2]
        sections["Evidence"] = "；".join(evidence_lines) if evidence_lines else "(none)"
    if not sections["NextStep"]:
        sections["NextStep"] = "(none)"

    return (
        f"Conclusion: {sections['Conclusion']}\n"
        f"Evidence: {sections['Evidence']}\n"
        f"NextStep: {sections['NextStep']}"
    )


def render_natural_reply(structured_text: str) -> str:
    """将字段式三段内容合并为自然段落。"""
    sections = {"Conclusion": "", "Evidence": "", "NextStep": ""}
    for line in [x.strip() for x in structured_text.splitlines() if x.strip()]:
        m = re.match(r"(?i)^(Conclusion|Evidence|NextStep)\s*:\s*(.*)$", line)
        if m:
            sections[m.group(1).title()] = m.group(2).strip()

    chunks = []
    if sections["Conclusion"] and sections["Conclusion"] != "(none)":
        chunks.append(sections["Conclusion"])
    if sections["Evidence"] and sections["Evidence"] != "(none)":
        chunks.append(f"依据是：{sections['Evidence']}")
    if sections["NextStep"] and sections["NextStep"] != "(none)":
        chunks.append(f"下一步建议：{sections['NextStep']}")
    if not chunks:
        return structured_text
    return " ".join(chunks)

def is_large_write_task(prompt: str) -> bool:
    text = str(prompt or "").lower()
    signals = ["sql", "脚本", "script", "insert", "10万", "100000", "100k", "mock", "模拟数据"]
    return any(k in text for k in signals)

def looks_like_heredoc_write(arg_text: str) -> bool:
    text = str(arg_text or "")
    return ("cat >" in text or "cat >>" in text) and "<<" in text
