import os
import ast
import json
import locale
import hashlib
import ast as py_ast
import re
import time
from pathlib import Path
# from openai import OpenAI

from yamlConfig import Config
from tools import tools_func, tools_title
from promptTemplate import (
    commonPrompt,
    initializationPrompt,
    systemPrefixPrompt,
    modePromptMap,
    toolboxPrompt,
    wikiPrompt,
)

# from pollTools import *
import pollTools as pts

from deepseek import DeepSeek


llmServer = {
    'deepseek': DeepSeek
}

class Polling():
    
    def __init__(self, verbose: bool = False, debug: bool = False, status_callback=None):
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
        self.config = Config(config_path)
        llm = self.config.get_nested("MODEL_SELECT", "model_name")
        self.api_key = self.config.get_nested(llm, "API_KEY")
        self.base_url = self.config.get_nested(llm, "BASE_URL", default="https://api.deepseek.com/v1")
        self.model = self.config.get_nested(llm, "MODEL", default="deepseek-chat")
        self.language = self.config.get_nested(llm, "LANGUAGE", default="Chinese")
        # Wiki/摘要相关配置，默认走轻量索引模式以控制输出体积。
        self.wiki_mode = self.config.get_nested(llm, "WIKI_MODE", default="index_only")
        self.summary_max_chars = self.config.get_nested(llm, "SUMMARY_MAX_CHARS", default=80)
        self.summary_sample_lines = self.config.get_nested(llm, "SUMMARY_SAMPLE_LINES", default=6)
        self.writefile_compact_default = self.config.get_nested(llm, "WRITEFILE_COMPACT_DEFAULT", default=True)
        self.system_prompt = "You are a helpful assistant."
        self._cached_system_prompt = {}
        self.history_limit = 20
        self.response_max_tokens_qa = int(self.config.get_nested(llm, "RESPONSE_MAX_TOKENS_QA", default=600))
        self.response_max_tokens_tool = int(self.config.get_nested(llm, "RESPONSE_MAX_TOKENS_TOOL", default=1200))
        self.response_max_tokens_code = int(self.config.get_nested(llm, "RESPONSE_MAX_TOKENS_CODE", default=1800))
        self.response_max_tokens_large_write = int(
            self.config.get_nested(llm, "RESPONSE_MAX_TOKENS_LARGE_WRITE", default=3200)
        )
        self.context_summary_max_chars = int(self.config.get_nested(llm, "CONTEXT_SUMMARY_MAX_CHARS", default=800))
        self.tool_retry_limit = int(self.config.get_nested(llm, "TOOL_RETRY_LIMIT", default=1))
        self.malformed_tool_call_retry_limit = int(
            self.config.get_nested(llm, "MALFORMED_TOOL_CALL_RETRY_LIMIT", default=2)
        )
        self.wiki_retrieval_top_k = int(self.config.get_nested(llm, "WIKI_RETRIEVAL_TOP_K", default=5))
        self.enable_wiki_retrieval = pts.parse_bool(self.config.get_nested(llm, "ENABLE_WIKI_RETRIEVAL", default=True))
        self.metrics_enabled = pts.parse_bool(self.config.get_nested(llm, "METRICS_ENABLED", default=True))
        self.enable_tool_docs_soft_check = pts.parse_bool(
            self.config.get_nested(llm, "ENABLE_TOOL_DOCS_SOFT_CHECK", default=True)
        )
        self.default_max_tools_per_round = int(self.config.get_nested(llm, "MAX_TOOLS_PER_ROUND", default=3))
        self.empty_reply_retry_limit = int(self.config.get_nested(llm, "EMPTY_REPLY_RETRY_LIMIT", default=2))
        self.read_file_allowlist = {
            x.strip().lower()
            for x in str(self.config.get_nested(llm, "READ_FILE_ALLOWLIST", default="")).split(",")
            if x.strip()
        }
        self.project_wiki_json_path = self.config.get_nested(llm, "PROJECT_WIKI_JSON_PATH", default="")
        self.metrics_file = str(
            self.config.get_nested(
                llm,
                "METRICS_FILE",
                default=str(Path(__file__).resolve().parents[1] / "runtime_metrics.jsonl"),
            )
        )
        # 将模型输出 token 上限和最终回复字符上限分离，避免语义混用。
        self.max_output_chars_qa = int(self.config.get_nested(llm, "MAX_OUTPUT_CHARS_QA", default=1200))
        self.max_output_chars_tool = int(self.config.get_nested(llm, "MAX_OUTPUT_CHARS_TOOL", default=1800))
        self.max_output_chars_code = int(self.config.get_nested(llm, "MAX_OUTPUT_CHARS_CODE", default=2800))
        self.tool_result_max_chars_default = int(self.config.get_nested(llm, "TOOL_RESULT_MAX_CHARS", default=1000))
        self.tool_result_max_chars_map = {
            "read_file": int(self.config.get_nested(llm, "READ_FILE_RESULT_MAX_CHARS", default=1200)),
            "list_dir": int(self.config.get_nested(llm, "LIST_DIR_RESULT_MAX_CHARS", default=900)),
            "cmd": int(self.config.get_nested(llm, "CMD_RESULT_MAX_CHARS", default=900)),
            "tool_docs": int(self.config.get_nested(llm, "TOOL_DOCS_RESULT_MAX_CHARS", default=1200)),
            "write_file": int(self.config.get_nested(llm, "WRITE_FILE_RESULT_MAX_CHARS", default=800)),
        }
        # print(f"使用模型: {llm}，API_KEY: {'已设置' if self.api_key else '未设置'}，BASE_URL: {self.base_url}，MODEL: {self.model}，LANGUAGE: {self.language}")
        

        # 保存每一轮的token使用情况，供 /status 查询和调试分析。
        self.metrics_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.metrics_rounds = []   # 每轮的明细列表

        # 初始化客户端
        self.client = llmServer[llm](
            api_key=self.api_key, 
            base_url=self.base_url, 
            model=self.model, 
            language=self.language,
            debug=debug,
        )

        # 只保存“真正参与对话”的消息，避免把大段模板重复塞进上下文。
        self.context = []
        self.context_summary = ""
        self.cmd_encoding = self.config.get_nested(llm, "CMD_ENCODING", default=locale.getpreferredencoding(False) or "utf-8")
        self.default_max_tool_rounds = int(self.config.get_nested(llm, "MAX_TOOL_ROUNDS", default=10))

        # 最大工具调用轮数和每轮最大工具调用数可以通过 /budget 命令动态调整，默认值从配置文件读取。
        self.max_tools_per_round = self.default_max_tools_per_round
        self.max_tool_rounds = self.default_max_tool_rounds

        # verbose 模式显示关键信息，debug 模式显示全部细节日志，默认都关闭。
        self.verbose = bool(verbose)
        self.debug = bool(debug)

        # 是否显示usgae信息和工具调用细节等调试日志，debug 模式默认开启，verbose 模式仅显示关键信息。
        self.show_usage =  False
        self.status_callback = status_callback
        self.last_intent_mode = "ask"
        self.mode_override = None

        if self.api_key is None:
            raise ValueError("未设置 API 密钥，请在环境变量中配置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY，或者在初始化时传入 api_key 参数。")

        # 预计算所有 mode 的 system_prompt，确保每次请求的 messages 开头部分完全一致
        tools_doc = self._format_tools_docs()
        for mode_name in ["ask", "plan", "agent"]:
            mode_prompt = modePromptMap.get(mode_name, "")
            self._cached_system_prompt[mode_name] = systemPrefixPrompt.format(
                common_prompt=commonPrompt,
                task_mode=mode_name,
                mode_prompt=mode_prompt,
                tools_prompt=toolboxPrompt.format(Toolbox=tools_doc),
            )

    def clear_context(self):
        """清除对话上下文"""
        self.context.clear()
        self.context_summary = ""

    def set_debug(self, enabled: bool):
        self.debug = bool(enabled)

    def set_show_usage(self, enabled: bool):
        self.show_usage = bool(enabled)

    def get_mode(self):
        return self.mode_override or self.last_intent_mode

    def set_mode(self, mode: str):
        value = str(mode or "").strip().lower()
        if value in {"", "auto"}:
            self.mode_override = None
            return "auto"
        if value not in {"ask", "plan", "agent"}:
            raise ValueError("mode 仅支持 ask|plan|agent|auto")
        self.mode_override = value
        self.last_intent_mode = value
        return value

    def set_tool_budget(self, max_tools_per_round=None, max_tool_rounds=None):
        if max_tools_per_round is not None:
            self.max_tools_per_round = max(1, int(max_tools_per_round))
        if max_tool_rounds is not None:
            self.max_tool_rounds = max(1, int(max_tool_rounds))

    def reset_tool_budget(self):
        self.max_tools_per_round = self.default_max_tools_per_round
        self.max_tool_rounds = self.default_max_tool_rounds

    def get_tool_budget(self):
        return {
            "max_tools_per_round": self.max_tools_per_round,
            "max_tool_rounds": self.max_tool_rounds,
            "defaults": {
                "max_tools_per_round": self.default_max_tools_per_round,
                "max_tool_rounds": self.default_max_tool_rounds,
            },
        }

    def _log(self, message: str, level: str = "info"):
        """统一日志出口：默认静默，debug 模式显示工具链细节。"""
        if level == "debug" and not self.debug:
            return
        if level == "info" and not self.verbose and not self.debug:
            return
        print(message)

    def _show_tool_indicator(self, tool_name: str):
        if self.debug:
            return
        # print(f"tools:{tool_name}")

    def _emit_status(self, message: str):
        """向外层 console 发状态信号。"""
        if callable(self.status_callback):
            try:
                self.status_callback(message)
            except Exception:
                pass
    
    def _is_sensitive_path(self, path_text: str) -> bool:
        if not path_text:
            return False
        normalized = str(path_text).replace("\\", "/").lower()
        basename = normalized.split("/")[-1]
        patterns = [".env", "config.yaml", "config.yml", "credentials.json", "secret", ".key", "apikey"]
        if basename in self.read_file_allowlist:
            return False
        return any(p in normalized for p in patterns)

    def _format_tools_docs(self):
        """把工具字典渲染成稳定的文本，避免把 dict 原样塞给模型。"""
        lines = []
        for tool_name, tool_desc in tools_title.items():
            lines.append(f"- {tool_name}: {tool_desc}")
        return "\n".join(lines)
    
    def _build_messages(self, prompt=None, intent_mode=None):
        """构建消息列表：使用缓存的 system_prompt 和历史对话；必要时再附加当前用户输入。"""
        messages = []
        mode = intent_mode or self.last_intent_mode
        cached = self._cached_system_prompt.get(mode, self.system_prompt)
        messages.append({"role": "system", "content": cached})
        if self.context_summary:
            messages.append({"role": "system", "content": f"Conversation summary:\n{self.context_summary}"})
        if self.history_limit > 0 and self.context:
            messages.extend(self.context[-self.history_limit * 2:])
        if prompt:
            messages.append({"role": "user", "content": prompt})
        return messages

    def _build_user_prompt(self, prompt, intent_mode: str):
        """把语言、工具说明和用户问题合成一条用户输入。"""
        wiki_context = self._build_wiki_context(prompt)
        write_hint = pts.build_write_strategy_hint(prompt)
        extra_context = f"\n\nRelevant wiki snippets:\n{wiki_context}" if wiki_context else ""
        return initializationPrompt.format(
            common_prompt=commonPrompt.format(
                system_info=pts.get_system_info(),
                language=self.language,
            ),
            task_mode=intent_mode,
            mode_prompt=modePromptMap.get(intent_mode, modePromptMap["ask"]),
            tools_prompt=toolboxPrompt.format(
                Toolbox=self._format_tools_docs(),
                current_dir=os.getcwd()
            ),
            question=f"{prompt}{extra_context}{write_hint}",
        )
    
    def _run_tool(self, tool_name, arg_text):
        """执行工具调用，并尽量把参数安全地还原成 Python 实参。"""
        if tool_name not in tools_func:
            return {"ok": False, "tool": tool_name, "error_type": "tool_not_found", "message": f"工具不存在: {tool_name}"}

        tool = tools_func[tool_name]
        if not arg_text:
            return self._normalize_tool_result(tool_name, tool())

        try:
            parsed_args, parsed_kwargs = self._parse_tool_arguments(arg_text)
        except (ValueError, SyntaxError):
            return {
                "ok": False,
                "tool": tool_name,
                "error_type": "invalid_arguments",
                "message": f"工具参数解析失败: {tool_name}({arg_text})",
            }

        parsed_args, parsed_kwargs = pts.coerce_tool_arguments(tool_name, parsed_args, parsed_kwargs)

        if tool_name == "write_file":
            has_protocol = ("edits" in parsed_kwargs) or ("code_chunk" in parsed_kwargs)
            if parsed_kwargs and not has_protocol:
                return {
                    "ok": False,
                    "tool": "write_file",
                    "error_type": "invalid_arguments",
                    "message": (
                        "write_file 参数形态不合法：需要 edits 或 code_chunk。"
                        f"收到键: {sorted(parsed_kwargs.keys())}"
                    ),
                    "hint": "推荐格式：write_file(file_path='...', edits='[{\"op\":\"insert\",\"s\":1,\"t\":\"...\"}]')",
                }

        if tool_name == "read_file":
            candidate_path = ""
            if parsed_args:
                candidate_path = str(parsed_args[0])
            elif "file_path" in parsed_kwargs:
                candidate_path = str(parsed_kwargs.get("file_path", ""))
            if self._is_sensitive_path(candidate_path):
                return self._normalize_tool_result(
                    tool_name,
                    {
                        "ok": False,
                        "tool": tool_name,
                        "error_type": "sensitive_file_blocked",
                        "error": f"安全策略阻止读取敏感文件: {candidate_path}",
                    },
                )

        if len(parsed_args) == 1 and not parsed_kwargs:
            try:
                return self._normalize_tool_result(tool_name, tool(parsed_args[0]))
            except Exception as err:
                return self._normalize_tool_result(
                    tool_name,
                    {"ok": False, "tool": tool_name, "error_type": "tool_runtime_error", "error": str(err)},
                )

        self._log(f"模型调用工具 {tool_name}，传入参数: args={parsed_args}, kwargs={parsed_kwargs}\n", level="debug")

        try:
            return self._normalize_tool_result(tool_name, tool(*parsed_args, **parsed_kwargs))
        except Exception as err:
            return self._normalize_tool_result(
                tool_name,
                {"ok": False, "tool": tool_name, "error_type": "tool_runtime_error", "error": str(err)},
            )
    
    def _parse_tool_arguments(self, arg_text):
        """解析工具参数，支持位置参数和关键字参数，仅允许 Python 字面量。"""
        expr = ast.parse(f"f({arg_text})", mode="eval")
        call = expr.body
        if not isinstance(call, ast.Call):
            raise ValueError("参数格式不是函数调用")

        args = tuple(ast.literal_eval(arg) for arg in call.args)

        kwargs = {}
        for keyword in call.keywords:
            # 不允许 **kwargs 这种动态展开，避免扩大解析面。
            if keyword.arg is None:
                raise ValueError("不支持 **kwargs 语法")
            kwargs[keyword.arg] = ast.literal_eval(keyword.value)

        return args, kwargs

    def _normalize_tool_result(self, tool_name, result):
        """统一工具返回结构，避免模型在下一轮消费非结构化文本。"""
        if isinstance(result, dict):
            normalized = dict(result)
            normalized.setdefault("tool", tool_name)
            normalized.setdefault("ok", "error" not in normalized)
            return self._sanitize_and_trim_tool_result(tool_name, normalized)
        return self._sanitize_and_trim_tool_result(tool_name, {"ok": True, "tool": tool_name, "result": result})

    def _sanitize_and_trim_tool_result(self, tool_name: str, payload: dict):
        """统一清洗并按工具类型裁剪 result/error/message 字段。"""
        max_chars = self.tool_result_max_chars_map.get(tool_name, self.tool_result_max_chars_default)
        normalized = dict(payload)
        if tool_name == "read_file" and "result" in normalized:
            normalized["result"] = self._normalize_read_file_result(normalized["result"])
        for key in ("result", "message", "error"):
            if key in normalized and normalized[key] is not None:
                cleaned = pts.clean_text(normalized[key])
                normalized[key] = pts.trim_result_text(cleaned, max_chars)
        return normalized

    def _normalize_read_file_result(self, value):
        """把 read_file 的列表字符串结果尽量还原为可读文本块。"""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return value
        try:
            parsed = py_ast.literal_eval(text)
        except Exception:
            return value
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return "".join(parsed)
        if isinstance(parsed, str):
            return parsed
        # literal_eval 失败时，尝试正则回退提取单引号字符串块。
        chunks = re.findall(r"'((?:[^'\\\\]|\\\\.)*)'", text)
        if chunks:
            rebuilt = "".join(bytes(x, "utf-8").decode("unicode_escape") for x in chunks)
            return rebuilt
        return value

    def _build_wiki_context(self, question: str) -> str:
        """从项目 wiki 索引中检索 top-k 片段，避免全量注入。"""
        if not self.enable_wiki_retrieval:
            return ""

        json_path = self._resolve_project_wiki_json_path()
        if json_path is None:
            return ""

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return ""

        files = data.get("files", [])
        if not files:
            return ""

        question_terms = {x.lower() for x in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+", question)}
        scored = []
        for item in files:
            text = " ".join(
                [
                    str(item.get("file", "")),
                    str(item.get("module", "")),
                    str(item.get("environment_range", "")),
                ]
            ).lower()
            score = sum(1 for t in question_terms if t and t in text)
            score += int(item.get("call_relation_count", 0) > 0)
            scored.append((score, item))

        top_items = [x[1] for x in sorted(scored, key=lambda t: t[0], reverse=True)[: self.wiki_retrieval_top_k] if x[0] > 0]
        if not top_items:
            return ""

        lines = []
        for item in top_items:
            lines.append(
                f"- file={item.get('file')} module={item.get('module')} chunks={item.get('chunk_count')} calls={item.get('call_relation_count')}"
            )
        return "\n".join(lines)

    def _resolve_project_wiki_json_path(self):
        """优先使用配置路径，其次按项目名匹配，最后才回退首个目录。"""
        if self.project_wiki_json_path:
            configured = Path(self.project_wiki_json_path)
            if configured.exists():
                return configured

        project_root = Path(__file__).resolve().parents[1]
        wiki_root = project_root / "wiki"
        if not wiki_root.exists():
            return None

        project_name = project_root.name
        by_name = wiki_root / project_name / "PROJECT_WIKI.json"
        if by_name.exists():
            return by_name

        project_folders = [p for p in wiki_root.iterdir() if p.is_dir()]
        if not project_folders:
            return None

        fallback = sorted(project_folders)[0] / "PROJECT_WIKI.json"
        if fallback.exists():
            self._log(f"[warn] wiki path fallback used: {fallback}", level="debug")
            return fallback
        return None

    def _maybe_update_context_summary(self):
        """上下文过长时压缩为摘要，保留最近窗口以降低后续 token。"""
        if len(self.context) <= self.history_limit * 2:
            return

        older = self.context[:-self.history_limit * 2]
        if not older:
            return

        merged = []
        for msg in older:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", "")).strip().replace("\n", " ")
            if content:
                merged.append(f"{role}: {content[:160]}")

        summary = " | ".join(merged)
        self.context_summary = (self.context_summary + " | " + summary).strip(" |")[: self.context_summary_max_chars]
        self.context = self.context[-self.history_limit * 2:]

    def _postprocess_reply(self, reply: str, max_output_chars: int, mode: str = "ask") -> str:
        """输出后处理：去冗余、压缩空白、保留结构化关键字段。"""
        text = (reply or "").strip()
        if not text:
            return text

        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"^(好的|当然|没问题)[，,。!！\s]*", "", text, flags=re.IGNORECASE)

        # 去掉重复行，减少无效输出。
        seen = set()
        lines = []
        for line in text.splitlines():
            key = line.strip()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            lines.append(line)
        text = "\n".join(lines)
        if mode == "ask":
            text = pts.enforce_three_section_format(text)
            text = pts.render_natural_reply(text)
        return text[: max_output_chars]

    def _choose_response_profile(self, prompt: str):
        p = (prompt or "").lower()
        if pts.is_large_write_task(prompt):
            return self.response_max_tokens_large_write, self.max_output_chars_code, "large_write"
        if any(k in p for k in ["代码", "code", "函数", "class", "bug", "报错"]):
            return self.response_max_tokens_code, self.max_output_chars_code, "code"
        if any(k in p for k in ["工具", "tool", "<tools>", "命令", "目录", "文件"]):
            return self.response_max_tokens_tool, self.max_output_chars_tool, "tool"
        return self.response_max_tokens_qa, self.max_output_chars_qa, "qa"


    def _record_metrics(self, payload: dict):
        # 记录工具调用和模型回复等事件到本地文件，供后续分析改进。敏感信息会被过滤掉。
        if not self.metrics_enabled:
            return
        try:
            path = Path(self.metrics_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            event = dict(payload)
            event.setdefault("ts", time.time())

            # 把会话级 token 汇总与按轮次明细附到事件中，优先使用当前 payload 的 usage
            try:
                event.setdefault("tokens", dict(self.metrics_totals))
                event.setdefault("token_rounds", list(self.metrics_rounds))
            except Exception:
                event["tokens"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                event["token_rounds"] = []

            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass
    
    def sendinfo(
        self,
        prompt,
        temperature=0.7,
        max_tokens=4000,
        max_tools_per_round=None,
        max_tool_rounds=None,
        mode=None,
    ):
        # 先把用户问题整理成完整任务说明，再进入模型轮转。
        if mode is not None:
            requested_mode = str(mode).strip().lower()
            if requested_mode not in {"ask", "plan", "agent"}:
                raise ValueError("mode 参数仅支持 ask|plan|agent")
            self.last_intent_mode = requested_mode
        elif self.mode_override in {"ask", "plan", "agent"}:
            self.last_intent_mode = self.mode_override
        else:
            self.last_intent_mode = pts.detect_intent_mode(prompt)

        effective_max_tools_per_round = self.max_tools_per_round if max_tools_per_round is None else max(1, int(max_tools_per_round))
        effective_max_tool_rounds = self.max_tool_rounds if max_tool_rounds is None else max(1, int(max_tool_rounds))

        user_prompt = self._build_user_prompt(prompt, self.last_intent_mode)
        self.context.append({"role": "user", "content": user_prompt})
        messages = self._build_messages(intent_mode=self.last_intent_mode)

        # 只允许有限轮工具调用，防止模型反复请求同一个工具导致死循环。
        selected_max_tokens, selected_max_output_chars, profile_name = self._choose_response_profile(prompt)
        tool_round_count = 0
        total_tool_calls = 0
        duplicate_tool_calls = 0
        total_tool_result_chars = 0
        invalid_arg_retries = 0
        malformed_tool_call_retries = 0
        empty_reply_retries = 0
        empty_reply_count = 0
        large_write_cmd_succeeded = False
        per_round_docs_seen = set()
        round_tool_cache = {}
        for _ in range(effective_max_tool_rounds):
            tool_round_count += 1

            # print(f"第 {_ + 1} 轮模型交互，当前上下文消息: {messages}\n\n")

            reply, usage_dict = self.client.sendinfo(
                messages=messages,
                temperature=temperature,
                max_tokens=min(max_tokens, selected_max_tokens),
            )
            # reply = response.choices[0].message.content.strip()

            # 解析并累加 token usage（兼容 provider 返回的 usage dict）
            try:
                u = usage_dict or {}
                prompt_tokens = int(u.get("prompt_tokens", 0) or 0)
                completion_tokens = int(u.get("completion_tokens", 0) or 0)
                total_tokens = int(u.get("total_tokens", 0) or 0)
            except Exception:
                prompt_tokens = completion_tokens = total_tokens = 0

            # 累加会话级 totals，并记录本轮明细
            try:
                self.metrics_totals["prompt_tokens"] += prompt_tokens
                self.metrics_totals["completion_tokens"] += completion_tokens
                self.metrics_totals["total_tokens"] += total_tokens
                self.metrics_rounds.append(
                    {
                        "round": tool_round_count,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    }
                )
            except Exception:
                pass

            self._log(f"[polling.debug] raw_reply_repr={repr(reply)}", level="debug")
            self._log(f"{reply}\n", level="debug")

            if pts.is_effectively_empty_reply(reply):
                empty_reply_count += 1
                if empty_reply_retries < self.empty_reply_retry_limit:
                    empty_reply_retries += 1
                    self._log(
                        f"模型返回空结果，正在重试生成最终回复... ({empty_reply_retries}/{self.empty_reply_retry_limit})",
                        level="info",
                    )
                    self._emit_status("模型正在重试生成最终回复...")
                    self._log(
                        f"[polling.debug] empty-reply retry triggered at round={tool_round_count}",
                        level="debug",
                    )
                    self.context.append(
                        {
                            "role": "user",
                            "content": (
                                "请立即输出非空最终回复或合法工具调用，禁止返回空内容。"
                                "如果你已经读取了关键文件，请直接给出可执行建议。"
                            ),
                        }
                    )
                messages = self._build_messages(intent_mode=self.last_intent_mode)
                    continue
                fallback = (
                    "模型暂时没有返回有效内容。建议你把目标拆成两步：先确认要修改的文件与范围，再要求输出具体改造步骤。"
                    if self.last_intent_mode == "agent"
                    else "模型暂时没有返回有效内容，请稍后重试。"
                )
                return fallback

            # 把模型输出写回上下文，后续轮次才能看到它做过什么判断。
            self.context.append({"role": "assistant", "content": reply})
            self._maybe_update_context_summary()

            tool_calls = pts.parse_tool_calls(reply)
            if "<tools>" in str(reply) and not tool_calls:
                # 剔出文端中非工具调用的部分，并输出
                print(reply.split("<tools>")[0].strip())

                malformed_tool_call_retries += 1
                if malformed_tool_call_retries > self.malformed_tool_call_retry_limit:
                    return (
                        "检测到连续工具调用格式异常（疑似长参数截断）。"
                        "请缩小单次 write_file 内容并分块写入，或提高 RESPONSE_MAX_TOKENS_CODE/TOOL 后重试。"
                    )
                self.context.append(
                    {
                        "role": "user",
                        "content": (
                            "工具调用格式错误：请严格使用 `<tools>tool_name(args)</tools>`，"
                            "仅输出一个合法工具调用，不要包含解释文本。"
                            "如果 write_file 内容较长，请分块写入，每次只提交一个较短的工具调用。"
                        ),
                    }
                )
                messages = self._build_messages(intent_mode=self.last_intent_mode)
                continue
            if not tool_calls:
                final_reply = self._postprocess_reply(reply, selected_max_output_chars, mode=self.last_intent_mode)
                self._record_metrics(
                    {
                        "event": "chat_complete",
                        "reply": reply,
                        "usage": usage_dict,
                        "profile": profile_name,
                        "tool_round_count": tool_round_count,
                        "max_tool_rounds": effective_max_tool_rounds,
                        "max_tools_per_round": effective_max_tools_per_round,
                        "tool_calls": total_tool_calls,
                        "duplicate_tool_calls": duplicate_tool_calls,
                        "duplicate_tool_call_rate": round(duplicate_tool_calls / total_tool_calls, 4) if total_tool_calls else 0.0,
                        "avg_tool_result_chars": round(total_tool_result_chars / total_tool_calls, 3) if total_tool_calls else 0.0,
                        "format_compliance_rate": 1.0 if final_reply.startswith("Conclusion:") and "\nEvidence:" in final_reply and "\nNextStep:" in final_reply else 0.0,
                        "empty_reply_count": empty_reply_count,
                        "intent_mode": self.last_intent_mode,
                        "reply_chars": len(final_reply),
                    }
                )
                return final_reply

            # print(f"调用工具: {[call['name'] for call in tool_calls]}")
            # 把工具调用结果回填给模型，进入下一轮继续生成最终回复。
            for tool_call in tool_calls[: effective_max_tools_per_round]:
                total_tool_calls += 1
                self._show_tool_indicator(tool_call["name"])
                cache_key = f"{tool_call['name']}::{tool_call['args']}"

                if (
                    profile_name == "large_write"
                    and large_write_cmd_succeeded
                    and tool_call["name"] == "write_file"
                ):
                    tool_result = {
                        "ok": False,
                        "tool": "write_file",
                        "error_type": "invalid_arguments",
                        "error": "大文件任务中已通过 cmd/heredoc 成功写入，禁止继续混用 write_file。",
                        "hint": "请改用 read_file 验证文件内容并直接给出总结。",
                    }
                    self.context.append(
                        {
                            "role": "user",
                            "content": (
                                "工具返回结果(JSON): "
                                + json.dumps(
                                    {
                                        "tool": tool_call["name"],
                                        "args": tool_call["args"],
                                        "result": tool_result,
                                    },
                                    ensure_ascii=False,
                                )
                            ),
                        }
                    )
                    total_tool_result_chars += len(json.dumps(tool_result, ensure_ascii=False))
                    self._log(f"工具结果: {tool_call['name']} -> \n{tool_result}", level="debug")
                    continue

                if cache_key in round_tool_cache:
                    duplicate_tool_calls += 1
                    tool_result = dict(round_tool_cache[cache_key])
                    tool_result["from_cache"] = True
                else:
                    tool_result = self._run_tool(tool_call["name"], tool_call["args"])
                    round_tool_cache[cache_key] = dict(tool_result) if isinstance(tool_result, dict) else {"ok": True, "result": tool_result}

                if (
                    profile_name == "large_write"
                    and tool_call["name"] == "cmd"
                    and isinstance(tool_result, dict)
                    and tool_result.get("ok") is True
                    and pts.looks_like_heredoc_write(tool_call.get("args", ""))
                ):
                    large_write_cmd_succeeded = True
                if tool_call["name"] == "tool_docs":
                    per_round_docs_seen.update(self._extract_tool_names_from_docs_args(tool_call["args"]))

                # 参数错误时不重复同参重试，而是让模型基于错误信息修正参数再调用。
                if (
                    isinstance(tool_result, dict)
                    and not tool_result.get("ok", True)
                    and tool_result.get("error_type") in {"invalid_arguments"}
                    and invalid_arg_retries < self.tool_retry_limit
                ):
                    invalid_arg_retries += 1
                    self.context.append(
                        {
                            "role": "user",
                            "content": (
                                "上一次工具调用参数无效，请修正参数后重新调用同一工具。"
                                f"错误详情: {json.dumps(tool_result, ensure_ascii=False)}"
                            ),
                        }
                    )
                    break

                if self.enable_tool_docs_soft_check and tool_call["name"] not in {"tool_docs"}:
                    if tool_call["name"] not in per_round_docs_seen:
                        if isinstance(tool_result, dict):
                            tool_result.setdefault("warnings", [])
                            tool_result["warnings"].append(
                                f"建议先调用 tool_docs('{tool_call['name']}') 再使用该工具。"
                            )

                self.context.append(
                    {
                        "role": "user",
                        "content": (
                            "工具返回结果(JSON): "
                            + json.dumps(
                                {
                                    "tool": tool_call["name"],
                                    "args": tool_call["args"],
                                    "result": tool_result,
                                },
                                ensure_ascii=False,
                            )
                        ),
                    }
                )
                total_tool_result_chars += len(json.dumps(tool_result, ensure_ascii=False))
                self._log(f"工具结果: {tool_call['name']} -> \n{tool_result}", level="debug")

                messages = self._build_messages(intent_mode=self.last_intent_mode)
            per_round_docs_seen.clear()
            round_tool_cache.clear()

        end_msg = "工具调用轮转次数已达上限，请检查模型是否在重复请求同一工具。"
        self._record_metrics(
            {
                "event": "tool_round_limit",
                "reply": reply,
                "usage": usage_dict,
                "profile": profile_name,
                "tool_round_count": effective_max_tool_rounds,
                "max_tool_rounds": effective_max_tool_rounds,
                "max_tools_per_round": effective_max_tools_per_round,
                "tool_calls": total_tool_calls,
                "duplicate_tool_calls": duplicate_tool_calls,
                "duplicate_tool_call_rate": round(duplicate_tool_calls / total_tool_calls, 4) if total_tool_calls else 0.0,
                "avg_tool_result_chars": round(total_tool_result_chars / total_tool_calls, 3) if total_tool_calls else 0.0,
                "reply_chars": len(end_msg),
            }
        )
        return end_msg

    def _extract_tool_names_from_docs_args(self, arg_text: str):
        """从 tool_docs 参数中提取工具名列表。"""
        try:
            args, _ = self._parse_tool_arguments(arg_text)
            if not args:
                return set()
            raw = str(args[0])
            return {x.strip() for x in raw.split(",") if x.strip()}
        except Exception:
            return set()

    
if __name__ == "__main__":
    polling = Polling()

    # print('project wiki successful!')
    polling.sendinfo("简要分析一下这个项目，并给出优化建议")
