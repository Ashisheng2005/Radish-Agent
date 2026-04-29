
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Symbol:
    kind: str
    name: str
    indent: int
    start_line: int
    end_line: int = 0
    parent_class: Optional[str] = None


class CreateCodeNodeExecutor:

    SYMBOL_RE = re.compile(r"^(?P<indent>\s*)(?P<kind>class|def)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")

    def __init__(
        self,
        code_file_path,
        wiki_file_path,
        llmServer,
        wiki_mode="index_only",
        max_chunk_lines=220,
        overlap_lines=15,
        short_chunk_line_threshold=40,
        short_chunk_batch_size=6,
        summary_max_workers=4,
        summary_max_chars=80,
        summary_sample_lines=6,
    ):
        self.code_file_path = code_file_path
        self.wiki_file_path = wiki_file_path
        self.llmServer = llmServer
        # wiki_mode:
        # - index_only: 默认轻量索引，避免产出过长
        # - hybrid: 保留有限详情
        # - full: 完整详情
        self.wiki_mode = wiki_mode if wiki_mode in {"index_only", "hybrid", "full"} else "index_only"
        self.max_chunk_lines = max(50, int(max_chunk_lines))
        self.overlap_lines = max(0, int(overlap_lines))
        self.short_chunk_line_threshold = max(5, int(short_chunk_line_threshold))
        self.short_chunk_batch_size = max(2, int(short_chunk_batch_size))
        self.summary_max_workers = max(1, int(summary_max_workers))
        self.summary_max_chars = max(30, int(summary_max_chars))
        self.summary_sample_lines = max(2, int(summary_sample_lines))

    def execute(self):
        '''主执行函数，返回生成的知识库数据结构'''
        with open(self.code_file_path, "r", encoding="utf-8") as code_file:
            code_content = code_file.read()

        lines = code_content.splitlines()
        symbols = self._scan_symbols(lines)
        chunks = self._build_chunks(lines, symbols)
        environment_range = self._detect_environment_range(lines, symbols)
        self._attach_call_relations(chunks, symbols)

        self._summarize_chunks(chunks)

        result = {
            "version": "v1",
            "source_file": self.code_file_path,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "environment_range": environment_range,
            "chunk_count": len(chunks),
            "chunks": chunks,
        }

        self._write_result(result)
        return result

    def _scan_symbols(self, lines: List[str]) -> List[Symbol]:
        symbols: List[Symbol] = []
        for idx, line in enumerate(lines, start=1):
            match = self.SYMBOL_RE.match(line)
            if not match:
                continue
            indent = len(match.group("indent"))
            symbols.append(
                Symbol(
                    kind=match.group("kind"),
                    name=match.group("name"),
                    indent=indent,
                    start_line=idx,
                )
            )

        if not symbols:
            return symbols

        total = len(lines)
        for i, symbol in enumerate(symbols):
            end_line = total
            for j in range(i + 1, len(symbols)):
                nxt = symbols[j]
                if nxt.indent <= symbol.indent:
                    end_line = nxt.start_line - 1
                    break
            symbol.end_line = max(symbol.start_line, end_line)

        classes = [s for s in symbols if s.kind == "class"]
        for symbol in symbols:
            if symbol.kind != "def":
                continue
            parent = None
            for cls in classes:
                if cls.start_line <= symbol.start_line <= cls.end_line and cls.indent < symbol.indent:
                    if parent is None or cls.start_line > parent.start_line:
                        parent = cls
            symbol.parent_class = parent.name if parent else None

        return symbols

    def _build_chunks(self, lines: List[str], symbols: List[Symbol]) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        if symbols:
            env_end = symbols[0].start_line - 1
            if env_end >= 1:
                env_lines = lines[:env_end]
                if any(x.strip() for x in env_lines):
                    chunks.append(
                        self._new_chunk(
                            symbol_name="environment_setup",
                            symbol_type="environment",
                            parent_class=None,
                            start_line=1,
                            end_line=env_end,
                            text="\n".join(env_lines),
                        )
                    )

        if not symbols:
            text = "\n".join(lines)
            chunks.append(
                self._new_chunk(
                    symbol_name="module",
                    symbol_type="module",
                    parent_class=None,
                    start_line=1,
                    end_line=max(1, len(lines)),
                    text=text,
                )
            )
            return chunks

        for symbol in symbols:
            symbol_lines = lines[symbol.start_line - 1:symbol.end_line]
            total = len(symbol_lines)

            if total <= self.max_chunk_lines:
                text = "\n".join(symbol_lines)
                chunks.append(
                    self._new_chunk(
                        symbol_name=symbol.name,
                        symbol_type=symbol.kind,
                        parent_class=symbol.parent_class,
                        start_line=symbol.start_line,
                        end_line=symbol.end_line,
                        text=text,
                    )
                )
                continue

            step = max(1, self.max_chunk_lines - self.overlap_lines)
            start = 0
            part = 1
            while start < total:
                end = min(total, start + self.max_chunk_lines)
                slice_lines = symbol_lines[start:end]
                chunk_text = "\n".join(slice_lines)
                chunks.append(
                    self._new_chunk(
                        symbol_name=f"{symbol.name}#part{part}",
                        symbol_type=symbol.kind,
                        parent_class=symbol.parent_class,
                        start_line=symbol.start_line + start,
                        end_line=symbol.start_line + end - 1,
                        text=chunk_text,
                    )
                )
                if end == total:
                    break
                start += step
                part += 1

        return chunks

    def _detect_environment_range(self, lines: List[str], symbols: List[Symbol]) -> str:
        if not lines:
            return "0-0"
        if not symbols:
            return f"1-{len(lines)}"
        end_line = max(0, symbols[0].start_line - 1)
        if end_line == 0:
            return "0-0"
        return f"1-{end_line}"

    def _new_chunk(
        self,
        symbol_name: str,
        symbol_type: str,
        parent_class: Optional[str],
        start_line: int,
        end_line: int,
        text: str,
    ) -> Dict[str, Any]:
        chunk_id = self._build_chunk_id(start_line=start_line, end_line=end_line, text=text)
        return {
            "chunk_id": chunk_id,
            "file_path": self.code_file_path,
            "symbol_name": symbol_name,
            "symbol_type": symbol_type,
            "parent_class": parent_class,
            "start_line": start_line,
            "end_line": end_line,
            "line_count": max(0, end_line - start_line + 1),
            "content": text,
            "summary": "",
        }

    def _build_chunk_id(self, start_line: int, end_line: int, text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        return f"{os.path.basename(self.code_file_path)}:{start_line}-{end_line}:{digest}"

    def _extract_symbol_base_name(self, symbol_name: str) -> str:
        return symbol_name.split("#part", 1)[0]

    def _find_called_custom_symbols(self, content: str, custom_symbols: set) -> List[str]:
        called = set()

        direct_calls = re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", content)
        member_calls = re.findall(r"(?:self|cls)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", content)

        for name in direct_calls + member_calls:
            if name in custom_symbols:
                called.add(name)

        return sorted(called)

    def _attach_call_relations(self, chunks: List[Dict[str, Any]], symbols: List[Symbol]):
        custom_symbols = {s.name for s in symbols if s.kind == "def"}

        symbol_to_chunk_ids: Dict[str, List[str]] = {}
        for chunk in chunks:
            base_name = self._extract_symbol_base_name(chunk["symbol_name"])
            chunk["symbol_base_name"] = base_name
            if chunk["symbol_type"] == "def" and base_name in custom_symbols:
                symbol_to_chunk_ids.setdefault(base_name, []).append(chunk["chunk_id"])

        for chunk in chunks:
            calls = self._find_called_custom_symbols(chunk.get("content", ""), custom_symbols)
            caller_base = chunk.get("symbol_base_name")
            if caller_base in calls:
                # 递归调用可选保留；这里默认保留，便于后续影响分析。
                pass

            chunk["called_custom_symbols"] = calls
            chunk["call_relations"] = [
                {
                    "from_chunk_id": chunk["chunk_id"],
                    "to_symbol": callee,
                    "to_chunk_ids": symbol_to_chunk_ids.get(callee, []),
                }
                for callee in calls
            ]

    def _summarize_chunks(self, chunks: List[Dict[str, Any]]):
        short_chunks: List[Dict[str, Any]] = []
        long_chunks: List[Dict[str, Any]] = []
        called_counter: Dict[str, int] = {}

        for chunk in chunks:
            for callee in chunk.get("called_custom_symbols", []):
                called_counter[callee] = called_counter.get(callee, 0) + 1

        for chunk in chunks:
            if chunk.get("line_count", 0) <= self.short_chunk_line_threshold:
                short_chunks.append(chunk)
            else:
                long_chunks.append(chunk)

        short_batches = [
            short_chunks[i:i + self.short_chunk_batch_size]
            for i in range(0, len(short_chunks), self.short_chunk_batch_size)
        ]

        # 阶段1：并发请求短块批次，先拿批量吞吐。
        if short_batches:
            workers = min(self.summary_max_workers, len(short_batches))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_batch = {
                    executor.submit(self._summarize_batch, batch): batch
                    for batch in short_batches
                }
                for future in as_completed(future_to_batch):
                    batch = future_to_batch[future]
                    summary_map: Dict[str, str] = {}
                    try:
                        summary_map = future.result() or {}
                    except Exception:
                        summary_map = {}

                    for chunk in batch:
                        chunk["summary"] = summary_map.get(chunk["chunk_id"], "")

        # 阶段2：并发降级补齐缺失摘要（短块失败项 + 全部长块）。
        fallback_targets = [chunk for chunk in short_chunks if not chunk.get("summary")]

        for chunk in long_chunks:
            # 对长块做采样：仅为“关键块”调用模型，其余用占位短句，显著降低 token
            base = chunk.get("symbol_base_name") or self._extract_symbol_base_name(chunk.get("symbol_name", ""))
            is_important = (
                chunk.get("symbol_type") == "environment"
                or bool(chunk.get("called_custom_symbols"))
                or called_counter.get(base, 0) > 0
            )
            if is_important:
                fallback_targets.append(chunk)
            else:
                chunk["summary"] = "辅助实现，按需查看源码。"

        if fallback_targets:
            workers = min(self.summary_max_workers, len(fallback_targets))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_chunk = {
                    executor.submit(self._summarize_chunk, chunk): chunk
                    for chunk in fallback_targets
                }
                for future in as_completed(future_to_chunk):
                    chunk = future_to_chunk[future]
                    try:
                        chunk["summary"] = future.result()
                    except Exception as err:
                        chunk["summary"] = f"摘要生成失败: {err}"

    def _summarize_batch(self, chunks: List[Dict[str, Any]]) -> Dict[str, str]:
        if not chunks:
            return {}

        payload_chunks = []
        for chunk in chunks:
            char_budget = self._summary_char_budget(chunk)
            payload_chunks.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "symbol": f"{chunk['symbol_type']} {chunk['symbol_name']}",
                    "line_range": f"{chunk['start_line']}-{chunk['end_line']}",
                    "summary_char_budget": char_budget,
                    "called_custom_symbols": chunk.get("called_custom_symbols", []),
                    # 仅传片段摘要上下文，不传完整代码，减少 token
                    "content_excerpt": self._build_content_excerpt(chunk.get("content", "")),
                }
            )

        prompt = (
            f"你是代码分析助手。请为多个代码块分别生成一句中文简述。每个块必须遵守 summary_char_budget 字段的字数上限。"
            "请结合 called_custom_symbols 给出描述。请严格返回 JSON 数组，不要 markdown，不要额外解释。"
            "JSON 格式: [{\"chunk_id\":\"...\",\"summary\":\"...\"}]\n\n"
            f"文件: {self.code_file_path}\n"
            f"代码块列表(JSON):\n{json.dumps(payload_chunks, ensure_ascii=False)}"
        )

        try:
            reply_text = self._call_llm(prompt)
            parsed = self._parse_json_payload(reply_text)
            if not isinstance(parsed, list):
                return {}

            summary_map: Dict[str, str] = {}
            budget_map = {chunk["chunk_id"]: self._summary_char_budget(chunk) for chunk in chunks}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                chunk_id = str(item.get("chunk_id", "")).strip()
                summary = str(item.get("summary", "")).strip()
                if chunk_id and summary:
                    summary_map[chunk_id] = summary[: budget_map.get(chunk_id, self.summary_max_chars)]
            return summary_map
        except Exception:
            return {}

    def _call_llm(self, prompt: str) -> Any:
        if hasattr(self.llmServer, "sendinfo") and callable(self.llmServer.sendinfo):
            messages = [{"role": "user", "content": prompt}]
            try:
                return self.llmServer.sendinfo(messages)
            except TypeError:
                # 兼容旧版 sendinfo(prompt: str) 形式
                return self.llmServer.sendinfo(prompt)
        if callable(self.llmServer):
            return self.llmServer(prompt)
        raise RuntimeError("LLM 接口不可用")

    def _parse_json_payload(self, value: Any) -> Any:
        if isinstance(value, (list, dict)):
            return value
        text = str(value).strip()
        if not text:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if fence:
            try:
                return json.loads(fence.group(1).strip())
            except json.JSONDecodeError:
                return None
        return None

    def _summarize_chunk(self, chunk: Dict[str, Any]) -> str:
        '''调用 LLM 生成代码块摘要，返回摘要文本'''
        char_budget = self._summary_char_budget(chunk)

        prompt = (
            f"你是代码分析助手。请对以下代码块给出一句中文简述（不超过{char_budget}字），突出功能或副作用。"
            "可以参考 called_custom_symbols。不要输出 Markdown 列表，不要复述代码。\n\n"
            f"文件: {chunk['file_path']}\n"
            f"符号: {chunk['symbol_type']} {chunk['symbol_name']}\n"
            f"行号: {chunk['start_line']}-{chunk['end_line']}\n"
            f"自实现调用: {', '.join(chunk.get('called_custom_symbols', [])) or '(none)'}\n"
            "代码摘要片段:\n"
            f"{self._build_content_excerpt(chunk.get('content', ''))}"
        )

        try:
            reply = self._call_llm(prompt)

            if isinstance(reply, dict):
                value = reply.get("message") or reply.get("content") or str(reply)
            else:
                value = str(reply)
            return value.strip()[:char_budget]
        except Exception as err:
            return f"摘要生成失败: {err}"

    def _summary_char_budget(self, chunk: Dict[str, Any]) -> int:
        """按重要度动态分配摘要长度：入口/编排块可略长，普通块更短。"""
        budget = self.summary_max_chars
        symbol_type = chunk.get("symbol_type")
        call_count = len(chunk.get("called_custom_symbols", []))

        if symbol_type == "environment":
            budget = min(120, max(budget, 90))
        elif symbol_type == "def" and call_count >= 2:
            budget = min(110, max(budget, 85))
        elif symbol_type == "def":
            budget = min(budget, 80)
        else:
            budget = min(budget, 70)
        return max(40, budget)

    def _build_content_excerpt(self, content: str) -> str:
        """构建低成本摘要输入片段：仅保留头尾少量行，减少 token。"""
        lines = (content or "").splitlines()
        if not lines:
            return ""

        take = min(self.summary_sample_lines, len(lines))
        if len(lines) <= take * 2:
            return "\n".join(lines)
        head = lines[:take]
        tail = lines[-take:]
        return "\n".join(head + ["..."] + tail)

    def _write_result(self, result: Dict[str, Any]):
        output_dir = os.path.dirname(os.path.abspath(self.wiki_file_path))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        if self.wiki_file_path.lower().endswith(".json"):
            with open(self.wiki_file_path, "w", encoding="utf-8") as wiki_file:
                wiki_file.write(json.dumps(result, ensure_ascii=False, indent=2))
            return

        total_called_symbols = sum(len(chunk.get("called_custom_symbols", [])) for chunk in result["chunks"])
        environment_chunk = next((chunk for chunk in result["chunks"] if chunk["symbol_type"] == "environment"), None)
        function_chunks = [chunk for chunk in result["chunks"] if chunk["symbol_type"] == "def"]
        class_chunks = [chunk for chunk in result["chunks"] if chunk["symbol_type"] == "class"]

        lines: List[str] = [
            f"# Code Wiki: {os.path.basename(self.code_file_path)}",
            "",
            "## Overview",
            "",
            f"- Source File: `{self.code_file_path}`",
            f"- Generated At: {result['generated_at']}",
            f"- Environment Range: {result.get('environment_range', '0-0')}",
            f"- Chunk Count: {result['chunk_count']}",
            f"- Function Chunks: {len(function_chunks)}",
            f"- Class Chunks: {len(class_chunks)}",
            f"- Call Relations: {total_called_symbols}",
            f"- Wiki Mode: {self.wiki_mode}",
            "",
            "## Recommended Reading Order",
            "",
            "1. Read the environment block first if `Environment Range` is not `0-0`.",
            "2. Read chunks with more `Called Custom Symbols` first, because they tend to be orchestration entry points.",
            "3. Then read the remaining function chunks in source order.",
            "4. Use the detailed chunk sections below only when you need metadata and call relations.",
            "",
        ]

        if environment_chunk:
            lines.extend(
                [
                "",
                "> Note: This wiki intentionally does not embed raw source code. Use the original file path and line range to jump back to source when needed.",
                    "## Environment Block",
                    "",
                    f"- Chunk ID: {environment_chunk['chunk_id']}",
                    f"- Lines: {environment_chunk['start_line']}-{environment_chunk['end_line']}",
                    "",
                    environment_chunk.get("summary") or "(empty)",
                    "",
                ]
            )

        lines.extend([
            "## Chunk Summary Table",
            "",
            "| Order | Symbol | Type | Lines | Calls | Summary |",
            "| --- | --- | --- | --- | --- | --- |",
        ])

        for index, chunk in enumerate(result["chunks"], start=1):
            summary_text = (chunk.get("summary") or "(empty)").replace("|", "\\|")
            calls_text = ", ".join(chunk.get("called_custom_symbols", [])) or "-"
            symbol_label = chunk["symbol_name"]
            lines.append(
                f"| {index} | {symbol_label} | {chunk['symbol_type']} | {chunk['start_line']}-{chunk['end_line']} | {calls_text} | {summary_text} |"
            )

        if self.wiki_mode == "index_only":
            lines.extend(
                [
                    "",
                    "> 当前为 index_only 模式：已省略详细 chunk 元数据与调用明细，以降低体积与 token 消耗。",
                    "",
                ]
            )
        else:
            lines.extend([
                "",
                "## Detailed Chunks",
                "",
            ])

        for chunk in result["chunks"]:
            if self.wiki_mode == "index_only":
                continue
            if self.wiki_mode == "hybrid" and chunk.get("line_count", 0) > self.short_chunk_line_threshold * 2:
                continue

            relations = chunk.get("call_relations", [])
            relations_text = "\n".join(
                f"- {relation['from_chunk_id']} -> {relation['to_symbol']} ({', '.join(relation['to_chunk_ids']) or 'unmapped'})"
                for relation in relations
            ) or "- (none)"

            lines.extend(
                [
                    f"### {chunk['symbol_type']} {chunk['symbol_name']}",
                    "",
                    "#### Metadata",
                    "",
                    f"- Chunk ID: {chunk['chunk_id']}",
                    f"- Lines: {chunk['start_line']}-{chunk['end_line']}",
                    f"- Parent Class: {chunk['parent_class']}",
                    f"- Called Custom Symbols: {', '.join(chunk.get('called_custom_symbols', [])) or '(none)'}",
                    "",
                    "#### Summary",
                    "",
                    chunk["summary"] or "(empty)",
                    "",
                    "#### Call Relations",
                    "",
                    relations_text,
                    "",
                ]
            )

        with open(self.wiki_file_path, "w", encoding="utf-8") as wiki_file:
            wiki_file.write("\n".join(lines))

        