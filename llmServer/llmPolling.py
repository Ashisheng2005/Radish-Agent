import os
import ast
import json
import locale
import hashlib
import re
import time
from pathlib import Path

from yamlConfig import Config
from tools import tools_func, tools_json, tool_docs, init_code_graph, sync_read_file_allowlist
from code_graph.gate import SymbolReadGate
from code_graph.store import CodeGraphStore
from promptTemplate import (
    commonPrompt,
    initializationPrompt,
    systemPrefixPrompt,
    modePromptMap,
    toolboxPrompt,
    wikiPrompt,
)

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
        self._llm_name = llm
        # Wiki/摘要相关配置，默认走轻量索引模式以控制输出体积。
        self.wiki_mode = self._cfg("WIKI_MODE", default="index_only")
        self.summary_max_chars = self._cfg("SUMMARY_MAX_CHARS", default=80, cast=int)
        self.summary_sample_lines = self._cfg("SUMMARY_SAMPLE_LINES", default=6, cast=int)
        self.writefile_compact_default = self._cfg("WRITEFILE_COMPACT_DEFAULT", default=True, cast=bool)
        self.system_prompt = "You are a helpful assistant."
        self._cached_system_prompt = {}
        self.history_limit = 20
        self.response_max_tokens_qa = self._cfg("RESPONSE_MAX_TOKENS_QA", default=600, cast=int)
        self.response_max_tokens_tool = self._cfg("RESPONSE_MAX_TOKENS_TOOL", default=1200, cast=int)
        self.response_max_tokens_code = self._cfg("RESPONSE_MAX_TOKENS_CODE", default=1800, cast=int)
        self.response_max_tokens_large_write = self._cfg("RESPONSE_MAX_TOKENS_LARGE_WRITE", default=3200, cast=int)
        self.context_summary_max_chars = self._cfg("CONTEXT_SUMMARY_MAX_CHARS", default=800, cast=int)
        self.tool_retry_limit = self._cfg("TOOL_RETRY_LIMIT", default=1, cast=int)
        self.malformed_tool_call_retry_limit = self._cfg("MALFORMED_TOOL_CALL_RETRY_LIMIT", default=2, cast=int)
        self.wiki_retrieval_top_k = self._cfg("WIKI_RETRIEVAL_TOP_K", default=5, cast=int)
        self.enable_wiki_retrieval = self._cfg("ENABLE_WIKI_RETRIEVAL", default=True, cast=bool)
        self.metrics_enabled = self._cfg("METRICS_ENABLED", default=True, cast=bool)
        self.enable_tool_docs_soft_check = self._cfg("ENABLE_TOOL_DOCS_SOFT_CHECK", default=True, cast=bool)
        self.default_max_tools_per_round = self._cfg("MAX_TOOLS_PER_ROUND", default=3, cast=int)
        self.empty_reply_retry_limit = self._cfg("EMPTY_REPLY_RETRY_LIMIT", default=2, cast=int)
        self.read_file_allowlist = {
            x.strip().lower()
            for x in str(self._cfg("READ_FILE_ALLOWLIST", default="")).split(",")
            if x.strip()
        }
        self.project_wiki_json_path = self._cfg("PROJECT_WIKI_JSON_PATH", default="")
        self.project_code_graph_json_path = self._cfg("PROJECT_CODE_GRAPH_JSON_PATH", default="")
        self.project_path = os.path.abspath(os.getcwd())
        self._symbol_gate_id = f"polling_{id(self)}"
        SymbolReadGate.set_active_gate_id(self._symbol_gate_id)
        self.metrics_file = str(
            self._cfg(
                "METRICS_FILE",
                default=str(Path(__file__).resolve().parents[1] / "runtime_metrics.jsonl"),
            )
        )
        # 将模型输出 token 上限和最终回复字符上限分离，避免语义混用。
        self.max_output_chars_qa = self._cfg("MAX_OUTPUT_CHARS_QA", default=1200, cast=int)
        self.max_output_chars_tool = self._cfg("MAX_OUTPUT_CHARS_TOOL", default=1800, cast=int)
        self.max_output_chars_code = self._cfg("MAX_OUTPUT_CHARS_CODE", default=2800, cast=int)
        self.tool_result_max_chars_default = self._cfg("TOOL_RESULT_MAX_CHARS", default=1000, cast=int)
        self.tool_result_max_chars_map = {
            "read_file": self._cfg("READ_FILE_RESULT_MAX_CHARS", default=1200, cast=int),
            "list_dir": self._cfg("LIST_DIR_RESULT_MAX_CHARS", default=900, cast=int),
            "cmd": self._cfg("CMD_RESULT_MAX_CHARS", default=900, cast=int),
            "tool_docs": self._cfg("TOOL_DOCS_RESULT_MAX_CHARS", default=1200, cast=int),
            "write_file": self._cfg("WRITE_FILE_RESULT_MAX_CHARS", default=800, cast=int),
            "search_symbols": self._cfg("SEARCH_SYMBOLS_RESULT_MAX_CHARS", default=1200, cast=int),
            "read_symbol": self._cfg("READ_SYMBOL_RESULT_MAX_CHARS", default=2400, cast=int),
            "write_symbol": self._cfg("WRITE_SYMBOL_RESULT_MAX_CHARS", default=1000, cast=int),
            "list_symbol_callers": self._cfg("LIST_SYMBOL_CALLERS_RESULT_MAX_CHARS", default=1500, cast=int),
            "grep_code": self._cfg("GREP_CODE_RESULT_MAX_CHARS", default=2000, cast=int),
        }
        sync_read_file_allowlist(self.read_file_allowlist)
        self.cmd_encoding = self._cfg("CMD_ENCODING", default=locale.getpreferredencoding(False) or "utf-8")
        # 保存每一轮的token使用情况，供 /status 查询和调试分析。
        self.metrics_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.metrics_rounds = []   # 每轮的明细列表

        # 初始化客户端
        self.client = llmServer.get(llm, DeepSeek)(
            api_key=self.api_key, 
            base_url=self.base_url, 
            model=self.model, 
            language=self.language,
            debug=debug,
        )

        # 只保存“真正参与对话”的消息，避免把大段模板重复塞进上下文。
        self.context = []
        self.context_summary = ""
        self.default_max_tool_rounds = self._cfg("MAX_TOOL_ROUNDS", default=10, cast=int)

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

        for mode_name in ["ask", "plan", "agent"]:
            mode_prompt = modePromptMap.get(mode_name, "")
            self._cached_system_prompt[mode_name] = systemPrefixPrompt.format(
                common_prompt=commonPrompt,
                task_mode=mode_name,
                mode_prompt=mode_prompt,
                tools_prompt=toolboxPrompt.format(current_dir=self.project_path),
            )

        self._code_graph_status = self.refresh_code_graph()

    def refresh_code_graph(self, project_path=None):
        """绑定项目路径并加载 CODE_GRAPH；供 console 启动与 /graph 命令调用。"""
        if project_path:
            self.project_path = os.path.abspath(project_path)
        init_code_graph(
            project_path=self.project_path,
            graph_path=self.project_code_graph_json_path,
        )
        resolved = self._resolve_code_graph_json_path()
        store = CodeGraphStore(self.project_path)
        status = {
            "loaded": False,
            "path": str(resolved) if resolved else store.graph_path,
            "project_path": self.project_path,
            "node_count": 0,
            "edge_count": 0,
            "parser_backend": "",
            "error": "",
        }
        if resolved is None or not resolved.exists():
            status["error"] = "未找到 CODE_GRAPH.json，请执行 /graph build"
            self._code_graph_status = status
            return status

        try:
            from code_graph.models import CodeGraphIndex

            graph = CodeGraphIndex.from_dict(json.loads(resolved.read_text(encoding="utf-8")))
            status.update(
                {
                    "loaded": True,
                    "path": str(resolved),
                    "node_count": graph.stats.get("node_count", len(graph.nodes)),
                    "edge_count": graph.stats.get("edge_count", len(graph.edges)),
                    "parser_backend": graph.parser_backend,
                }
            )
        except Exception as err:
            status["error"] = str(err)
        self._code_graph_status = status
        return status

    def build_code_graph(self, wiki_root=None):
        """为当前 project_path 构建代码图索引。"""
        from code_graph.indexer import CodeGraphIndexer

        t0 = time.time()
        index = CodeGraphIndexer(self.project_path, wiki_root=wiki_root).execute()
        status = self.refresh_code_graph()
        status["built"] = True
        status["elapsed_sec"] = round(time.time() - t0, 2)
        status["node_count"] = index.stats.get("node_count", len(index.nodes))
        status["edge_count"] = index.stats.get("edge_count", len(index.edges))
        status["parser_backend"] = index.parser_backend
        self._code_graph_status = status
        return status

    def get_code_graph_status(self):
        return getattr(self, "_code_graph_status", {}) or self.refresh_code_graph()

    def clear_symbol_read_gate(self):
        SymbolReadGate.get(self._symbol_gate_id).clear()

    def _cfg(self, key: str, default=None, cast=None):
        val = self.config.get_nested(self._llm_name, key, default=default)
        if cast == int:
            return int(val)
        if cast == bool:
            return pts.parse_bool(val)
        return val

    def clear_context(self):
        """清除对话上下文"""
        self.context.clear()
        self.context_summary = ""
        SymbolReadGate.get(self._symbol_gate_id).clear()
        self._reset_tool_session_state()

    def add_read_file_allowlist(self, *names):
        """允许读取 config.yaml 等（配置审计）。"""
        for name in names:
            n = str(name).strip().lower()
            if n:
                self.read_file_allowlist.add(n)
        sync_read_file_allowlist(self.read_file_allowlist)

    def _reset_tool_session_state(self):
        self._session_tool_signatures: list = []
        self._empty_search_streak = 0
        self._cmd_findstr_count = 0
        self._grep_code_count = 0
        self._read_symbol_files: set = set()
        self._read_file_paths: set = set()
        self._investigation_evidence = {
            "search_hit": False,
            "read_small_file": False,
            "grep_usage": False,
        }
        self._evidence_stop_injected = False

    def _normalize_tool_signature(self, tool_name: str, call_info: dict) -> str:
        if call_info.get("is_native"):
            kwargs = call_info.get("kwargs") or {}
            try:
                payload = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
            except TypeError:
                payload = str(kwargs)
            return f"{tool_name}::{payload}"
        return f"{tool_name}::{call_info.get('args', '')}"

    def _check_tool_loop_policy(self, tool_name: str, signature: str, arg_text: str, call_info: dict | None = None) -> dict | None:
        if self._session_tool_signatures and self._session_tool_signatures[-1] == signature:
            return {
                "ok": False,
                "tool": tool_name,
                "error_type": "duplicate_loop",
                "error": "检测到与上一轮完全相同的工具调用，已跳过。请换用 hints 中的 next_steps 或直接总结。",
                "hint": "勿重复 search_symbols/cmd；改用 list_symbol_callers 或 grep_code",
            }
        if tool_name == "cmd" and "findstr" in str(arg_text).lower():
            self._cmd_findstr_count += 1
            if self._cmd_findstr_count > 1:
                return {
                    "ok": False,
                    "tool": tool_name,
                    "error_type": "duplicate_loop",
                    "error": "已执行过 findstr 类 cmd，请改用 grep_code_batch(preset='find_config_loader') 一次完成文本搜索。",
                }
        if tool_name in {"grep_code", "grep_code_batch"}:
            self._grep_code_count += 1
            grep_limit = self._cfg("MAX_GREP_CODE_PER_SESSION", default=2, cast=int)
            if self._grep_code_count > grep_limit:
                return {
                    "ok": False,
                    "tool": tool_name,
                    "error_type": "duplicate_loop",
                    "error": (
                        f"本会话已调用 grep 类工具 {self._grep_code_count - 1} 次。"
                        "请改用 grep_code_batch(preset='find_config_loader') 或 list_module_importers，然后直接总结。"
                    ),
                    "hint": "勿对同一调查连续换 pattern 调用 grep_code",
                }
        if call_info:
            read_block = self._check_read_symbol_loop(tool_name, call_info)
            if read_block is not None:
                return read_block
        return None

    def _normalize_file_key(self, file_path: str) -> str:
        p = str(file_path or "").strip().replace("\\", "/")
        if not p:
            return ""
        if not os.path.isabs(p):
            return p.lower()
        try:
            return os.path.relpath(p, self.project_path).replace("\\", "/").lower()
        except ValueError:
            return p.lower()

    def _check_read_symbol_loop(self, tool_name: str, call_info: dict) -> dict | None:
        if tool_name not in {"read_symbol", "read_file"}:
            return None
        if call_info.get("is_native"):
            kwargs = call_info.get("kwargs") or {}
            fp = kwargs.get("file_path", "")
        else:
            fp = ""
            try:
                import ast as _ast

                parsed = _ast.literal_eval(call_info.get("args") or "()")
                if isinstance(parsed, dict):
                    fp = parsed.get("file_path", "")
                elif isinstance(parsed, (list, tuple)) and parsed:
                    fp = parsed[0]
            except Exception:
                fp = ""
        key = self._normalize_file_key(fp)
        if not key:
            return None
        if tool_name == "read_symbol" and key in self._read_symbol_files:
            return {
                "ok": False,
                "tool": tool_name,
                "error_type": "duplicate_loop",
                "error": f"本会话已 read_symbol 过 {fp}。小文件请一次 read_file；或只对入口符号 read_symbol(include_neighbors=True) 一次。",
                "hint": "勿对同文件 __init__/get/get_nested 各调一次 read_symbol",
            }
        return None

    def _record_read_paths(self, tool_name: str, tool_result: dict, call_info: dict) -> None:
        if not isinstance(tool_result, dict) or not tool_result.get("ok"):
            return
        fp = tool_result.get("file_path") or tool_result.get("target", {}).get("file_path", "")
        if not fp and call_info.get("is_native"):
            fp = (call_info.get("kwargs") or {}).get("file_path", "")
        key = self._normalize_file_key(fp)
        if not key:
            return
        if tool_name == "read_symbol":
            self._read_symbol_files.add(key)
        if tool_name == "read_file":
            self._read_file_paths.add(key)
            line_count = int(tool_result.get("line_count") or tool_result.get("lines") or 0)
            if line_count and line_count <= 120:
                self._investigation_evidence["read_small_file"] = True
            elif len(str(tool_result.get("content", ""))) < 8000:
                self._investigation_evidence["read_small_file"] = True

    def _after_tool_result_policy(self, tool_name: str, tool_result: dict, call_info: dict | None = None) -> None:
        if tool_name == "search_symbols" and isinstance(tool_result, dict):
            if tool_result.get("ok") and int(tool_result.get("count", 0) or 0) == 0:
                self._empty_search_streak += 1
            else:
                self._empty_search_streak = 0
                if int(tool_result.get("count", 0) or 0) > 0:
                    self._investigation_evidence["search_hit"] = True
        if self._empty_search_streak >= 3:
            self.context.append(
                {
                    "role": "user",
                    "content": (
                        "已连续 3 次 search_symbols 无结果。请停止换词搜索。"
                        "改用: grep_code_batch(preset='find_config_loader'), read_file(yamlConfig.py), "
                        "list_module_importers('yamlConfig.py')。然后直接给出分析结论。"
                    ),
                }
            )
            self._empty_search_streak = 0

        if tool_name in {"grep_code", "grep_code_batch"} and isinstance(tool_result, dict) and tool_result.get("ok"):
            usage_n = int(tool_result.get("usage_reference_count", 0) or 0)
            if usage_n > 0 or tool_result.get("warnings"):
                self._investigation_evidence["grep_usage"] = True
            for item in tool_result.get("hits") or []:
                text = str(item.get("text", "")).lower()
                if "import yamlconfig" in text or "config(" in text.replace(" ", ""):
                    self._investigation_evidence["grep_usage"] = True
                    break

        if call_info:
            self._record_read_paths(tool_name, tool_result, call_info)

        ev = self._investigation_evidence
        if (
            not self._evidence_stop_injected
            and ev.get("search_hit")
            and ev.get("grep_usage")
            and (ev.get("read_small_file") or self._read_symbol_files)
        ):
            self._evidence_stop_injected = True
            self.context.append(
                {
                    "role": "user",
                    "content": (
                        "调查证据已足够（符号命中 + 读取源码 + import/Config 引用）。"
                        "请直接撰写结论，勿再调用 grep/read 工具。"
                        "若 grep 有 warnings，勿称 Config 为死代码。"
                    ),
                }
            )

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

    def get_max_token(self) -> int:
        """获取当前提供商配置的 MAX_TOKEN。"""
        return self._cfg("MAX_TOKEN", default=128000, cast=int)

    def get_available_providers(self) -> list[str]:
        """返回 config.yaml 中所有已配置的提供商名称。"""
        config = self.config.config
        exclude = {"MODEL_SELECT"}
        return [k for k in config if isinstance(config.get(k), dict) and k not in exclude]

    def switch_provider(self, provider_name: str) -> str:
        """切换到另一个已配置的提供商，重新初始化客户端。"""
        config = self.config.config
        provider = config.get(provider_name)
        if not provider or not isinstance(provider, dict):
            raise ValueError(f"提供商「{provider_name}」未在 config.yaml 中配置")

        api_key = provider.get("API_KEY")
        base_url = provider.get("BASE_URL")
        model = provider.get("MODEL")
        language = provider.get("LANGUAGE", "Chinese")

        if not all([api_key, base_url, model]):
            raise ValueError(f"提供商「{provider_name}」配置不完整（缺少 API_KEY/BASE_URL/MODEL）")

        # 更新实例状态
        self._llm_name = provider_name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.language = language

        # 重新读取该提供商的各项配置
        self.response_max_tokens_qa = self._cfg("RESPONSE_MAX_TOKENS_QA", default=600, cast=int)
        self.response_max_tokens_tool = self._cfg("RESPONSE_MAX_TOKENS_TOOL", default=1200, cast=int)
        self.response_max_tokens_code = self._cfg("RESPONSE_MAX_TOKENS_CODE", default=1800, cast=int)
        self.response_max_tokens_large_write = self._cfg("RESPONSE_MAX_TOKENS_LARGE_WRITE", default=3200, cast=int)

        # 重新初始化客户端
        self.client = llmServer.get(provider_name, DeepSeek)(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            language=self.language,
            debug=self.debug,
        )

        # 持久化 MODEL_SELECT
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
        config["MODEL_SELECT"] = {"model_name": provider_name}
        import yaml
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return provider_name

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
            tools_prompt=toolboxPrompt.format(current_dir=os.getcwd()),
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
            parsed = ast.literal_eval(text)
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
        """从 CODE_GRAPH 符号索引检索 top-k 节点；无图时回退文件级 wiki。"""
        if not self.enable_wiki_retrieval:
            return ""

        graph_path = self._resolve_code_graph_json_path()
        if graph_path is not None:
            try:
                from code_graph.models import CodeGraphIndex

                graph = CodeGraphIndex.from_dict(json.loads(graph_path.read_text(encoding="utf-8")))
                question_terms = {x.lower() for x in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+", question)}
                scored = []
                for node in graph.nodes:
                    hay = " ".join(
                        [node.qualified_name, node.file_path, node.summary, node.kind, node.language]
                    ).lower()
                    score = sum(1 for t in question_terms if t and t in hay)
                    score += min(2, len(node.called_by))
                    score += min(1, len(node.calls))
                    if score > 0:
                        scored.append((score, node))
                top_nodes = [x[1] for x in sorted(scored, key=lambda t: t[0], reverse=True)[: self.wiki_retrieval_top_k]]
                if top_nodes:
                    lines = ["Relevant code graph symbols (read these before editing):"]
                    for node in top_nodes:
                        lines.append(
                            f"- {node.qualified_name} @ {node.file_path}:{node.start_line}-{node.end_line} "
                            f"calls={len(node.calls)} called_by={len(node.called_by)} hash={node.body_hash}"
                        )
                        if node.called_by:
                            lines.append("  upstream_ids=" + ",".join(node.called_by[:3]))
                        if node.calls:
                            lines.append("  downstream_ids=" + ",".join(node.calls[:3]))
                    return "\n".join(lines)
            except Exception:
                pass

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

    def _resolve_code_graph_json_path(self):
        if self.project_code_graph_json_path:
            configured = Path(self.project_code_graph_json_path)
            if configured.exists():
                return configured
        return CodeGraphStore.resolve_graph_path(self.project_path, self.project_code_graph_json_path)

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
        if pts.is_investigation_task(prompt):
            inv_rounds = self._cfg("INVESTIGATION_MAX_TOOL_ROUNDS", default=12, cast=int)
            effective_max_tool_rounds = max(effective_max_tool_rounds, inv_rounds)

        self._reset_tool_session_state()

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

            reply, reply_tool_calls, usage_dict = self.client.sendinfo(
                messages=messages,
                tools=tools_json,
                temperature=temperature,
                max_tokens=min(max_tokens, selected_max_tokens),
            )

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

            # ---- 工具调用检测：原生 tools > XML fallback ----
            native_calls_parsed = []
            if reply_tool_calls:
                for tc in reply_tool_calls:
                    native_calls_parsed.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })

            xml_tool_calls = pts.parse_tool_calls(reply or "") if not native_calls_parsed else []

            # ---- 构建 assistant 消息 ----
            assistant_entry = {"role": "assistant", "content": reply}
            if native_calls_parsed:
                assistant_entry["tool_calls"] = [
                    {
                        "id": nc["id"],
                        "type": "function",
                        "function": {"name": nc["name"], "arguments": nc["arguments"]},
                    }
                    for nc in native_calls_parsed
                ]

            # ---- 无任何工具调用 => 最终回复 ----
            if not native_calls_parsed and not xml_tool_calls:
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

                self.context.append(assistant_entry)
                self._maybe_update_context_summary()
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

            # ---- 格式异常的 XML 工具调用 ----
            if not native_calls_parsed and "<tools>" in str(reply) and not xml_tool_calls:
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

            # ---- 有工具调用: 构建统一 execution_list ----
            self.context.append(assistant_entry)
            self._maybe_update_context_summary()

            if native_calls_parsed:
                execution_list = []
                for nc in native_calls_parsed:
                    try:
                        kwargs = json.loads(nc["arguments"])
                    except json.JSONDecodeError:
                        kwargs = {}
                    execution_list.append({
                        "id": nc["id"],
                        "name": nc["name"],
                        "args": nc["arguments"],
                        "kwargs": kwargs,
                        "is_native": True,
                    })
            else:
                execution_list = [
                    {
                        "id": f"call_xml_{i}",
                        "name": xc["name"],
                        "args": xc["args"],
                        "kwargs": None,
                        "is_native": False,
                    }
                    for i, xc in enumerate(xml_tool_calls)
                ]

            for call_info in execution_list[:effective_max_tools_per_round]:
                tool_name = call_info["name"]
                total_tool_calls += 1
                self._show_tool_indicator(tool_name)
                cache_key = f"{tool_name}::{call_info['args']}"
                signature = self._normalize_tool_signature(tool_name, call_info)
                arg_text = call_info.get("args") or json.dumps(call_info.get("kwargs") or {}, ensure_ascii=False)

                loop_block = self._check_tool_loop_policy(tool_name, signature, arg_text, call_info)
                if loop_block is not None:
                    duplicate_tool_calls += 1
                    tool_result = loop_block
                    self._append_tool_result(call_info, tool_result)
                    total_tool_result_chars += len(json.dumps(tool_result, ensure_ascii=False))
                    self._session_tool_signatures.append(signature)
                    self._after_tool_result_policy(tool_name, tool_result, call_info)
                    messages = self._build_messages(intent_mode=self.last_intent_mode)
                    continue

                if (
                    profile_name == "large_write"
                    and large_write_cmd_succeeded
                    and tool_name == "write_file"
                ):
                    tool_result = {
                        "ok": False,
                        "tool": "write_file",
                        "error_type": "invalid_arguments",
                        "error": "大文件任务中已通过 cmd/heredoc 成功写入，禁止继续混用 write_file。",
                        "hint": "请改用 read_file 验证文件内容并直接给出总结。",
                    }
                    self._append_tool_result(call_info, tool_result)
                    total_tool_result_chars += len(json.dumps(tool_result, ensure_ascii=False))
                    self._log(f"工具结果: {tool_name} -> \n{tool_result}", level="debug")
                    continue

                if cache_key in round_tool_cache:
                    duplicate_tool_calls += 1
                    tool_result = dict(round_tool_cache[cache_key])
                    tool_result["from_cache"] = True
                else:
                    if call_info["is_native"]:
                        tool_result = self._run_tool_from_native(tool_name, call_info["kwargs"])
                    else:
                        tool_result = self._run_tool(tool_name, call_info["args"])
                    round_tool_cache[cache_key] = dict(tool_result) if isinstance(tool_result, dict) else {"ok": True, "result": tool_result}

                if (
                    profile_name == "large_write"
                    and tool_name == "cmd"
                    and isinstance(tool_result, dict)
                    and tool_result.get("ok") is True
                    and pts.looks_like_heredoc_write(str(call_info.get("args", "")))
                ):
                    large_write_cmd_succeeded = True

                if tool_name == "tool_docs":
                    if call_info["is_native"]:
                        per_round_docs_seen.update(
                            self._extract_tool_names_from_args(call_info["args"], is_native=True)
                        )
                    else:
                        per_round_docs_seen.update(
                            self._extract_tool_names_from_args(call_info["args"], is_native=False)
                        )

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

                if self.enable_tool_docs_soft_check and tool_name not in {"tool_docs"}:
                    if tool_name not in per_round_docs_seen:
                        if isinstance(tool_result, dict):
                            tool_result.setdefault("warnings", [])
                            tool_result["warnings"].append(
                                f"建议先调用 tool_docs('{tool_name}') 再使用该工具。"
                            )

                self._append_tool_result(call_info, tool_result)
                total_tool_result_chars += len(json.dumps(tool_result, ensure_ascii=False))
                self._log(f"工具结果: {tool_name} -> \n{tool_result}", level="debug")

                self._session_tool_signatures.append(signature)
                if isinstance(tool_result, dict):
                    self._after_tool_result_policy(tool_name, tool_result, call_info)

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

    def _extract_tool_names_from_args(self, arg_text: str, is_native: bool = False):
        if is_native:
            try:
                kwargs = json.loads(arg_text)
                tools_str = kwargs.get("tools_name", "")
                return {x.strip() for x in tools_str.split(",") if x.strip()}
            except (json.JSONDecodeError, AttributeError):
                return set()
        try:
            args, _ = self._parse_tool_arguments(arg_text)
            if not args:
                return set()
            raw = str(args[0])
            return {x.strip() for x in raw.split(",") if x.strip()}
        except Exception:
            return set()

    def _run_tool_from_native(self, tool_name, kwargs_dict):
        """从原生 API function calling 的 JSON dict 参数执行工具。"""
        if tool_name not in tools_func:
            return {"ok": False, "tool": tool_name, "error_type": "tool_not_found",
                    "message": f"工具不存在: {tool_name}"}

        tool = tools_func[tool_name]

        if tool_name == "tool_docs":
            tools_name_str = kwargs_dict.get("tools_name", "")
            try:
                return self._normalize_tool_result(tool_name, tool_docs(tools_name_str))
            except Exception as err:
                return self._normalize_tool_result(
                    tool_name,
                    {"ok": False, "tool": tool_name, "error_type": "tool_runtime_error", "error": str(err)},
                )

        _, coerced_kwargs = pts.coerce_tool_arguments(tool_name, (), kwargs_dict)

        if tool_name == "read_file":
            candidate_path = coerced_kwargs.get("file_path", "")
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

        try:
            return self._normalize_tool_result(tool_name, tool(**coerced_kwargs))
        except Exception as err:
            return self._normalize_tool_result(
                tool_name,
                {"ok": False, "tool": tool_name, "error_type": "tool_runtime_error", "error": str(err)},
            )

    def _append_tool_result(self, call_info: dict, tool_result):
        """将工具执行结果追加到上下文。原生调用用 tool role，XML fallback 用 user role。"""
        result_json = json.dumps({
            "tool": call_info["name"],
            "args": call_info["args"],
            "result": tool_result,
        }, ensure_ascii=False)

        if call_info.get("is_native"):
            self.context.append({
                "role": "tool",
                "tool_call_id": call_info["id"],
                "content": result_json,
            })
        else:
            self.context.append({
                "role": "user",
                "content": f"工具返回结果(JSON): {result_json}",
            })

    
if __name__ == "__main__":
    polling = Polling()
    polling.sendinfo("简要分析一下这个项目，并给出优化建议")
