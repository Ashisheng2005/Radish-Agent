
import json
import os
from datetime import datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional

from CreateCodeNode import CreateCodeNodeExecutor


class CreateProjectWikiExecutor:
    """项目级 Wiki 生成器。

    流程:
    1) 遍历项目内 .py 文件（排除虚拟环境与构建目录）
    2) 在 wiki 目录镜像创建同名层级
    3) 调用 CreateCodeNodeExecutor 生成单文件 wiki
    4) 汇总生成 PROJECT_WIKI.md 和 PROJECT_WIKI.json
    """

    DEFAULT_IGNORE_DIRS = {
        ".git",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "venv",
        ".venv",
        "env",
        ".env",
        "node_modules",
        "dist",
        "build",
        "site-packages",
        "wiki",
        "wiki_output",
    }

    def __init__(
        self,
        project_path: str,
        llm_server,
        wiki_root: Optional[str] = None,
        ignore_dirs: Optional[List[str]] = None,
        wiki_mode: str = "index_only",
        summary_max_chars: int = 80,
        summary_sample_lines: int = 6,
    ):
        self.project_path = os.path.abspath(project_path)
        self.llm_server = llm_server
        self.wiki_root = os.path.abspath(wiki_root or os.path.join(self.project_path, "wiki"))
        self.project_name = os.path.basename(self.project_path.rstrip(os.sep)) or "project"
        self.project_wiki_root = os.path.join(self.wiki_root, self.project_name)
        self.ignore_dirs = set(ignore_dirs or []) | self.DEFAULT_IGNORE_DIRS
        self.wiki_mode = wiki_mode
        self.summary_max_chars = int(summary_max_chars)
        self.summary_sample_lines = int(summary_sample_lines)

    def execute(self) -> Dict[str, Any]:
        if not os.path.isdir(self.project_path):
            raise ValueError(f"project_path 不存在或不是目录: {self.project_path}")
        if self.llm_server is None:
            raise ValueError("llm_server 不能为空，请传入可调用的模型客户端")

        os.makedirs(self.project_wiki_root, exist_ok=True)

        py_files = self._collect_python_files()
        file_results: List[Dict[str, Any]] = []
        failed_files: List[Dict[str, str]] = []

        for file_path in py_files:
            rel_path = os.path.relpath(file_path, self.project_path)
            wiki_path = self._build_wiki_file_path(rel_path)
            os.makedirs(os.path.dirname(wiki_path), exist_ok=True)

            try:
                # CreateCodeNodeExecutor 负责单文件分块、摘要和调用关系。
                per_file_result = CreateCodeNodeExecutor(
                    code_file_path=file_path,
                    wiki_file_path=wiki_path,
                    llmServer=self.llm_server,
                    wiki_mode=self.wiki_mode,
                    summary_max_chars=self.summary_max_chars,
                    summary_sample_lines=self.summary_sample_lines,
                ).execute()
                module_name = self._module_name_from_relative_path(rel_path)
                call_relation_count = sum(
                    len(chunk.get("call_relations", []))
                    for chunk in per_file_result.get("chunks", [])
                )
                file_results.append(
                    {
                        "file": rel_path,
                        "module": module_name,
                        "wiki_file": os.path.relpath(wiki_path, self.project_path),
                        "chunk_count": per_file_result.get("chunk_count", 0),
                        "environment_range": per_file_result.get("environment_range", "0-0"),
                        "call_relation_count": call_relation_count,
                    }
                )
            except Exception as err:
                failed_files.append({"file": rel_path, "error": str(err)})

        project_result = {
            "project_path": self.project_path,
            "wiki_root": self.wiki_root,
            "project_wiki_root": self.project_wiki_root,
            "project_name": self.project_name,
            "wiki_mode": self.wiki_mode,
            "summary_max_chars": self.summary_max_chars,
            "summary_sample_lines": self.summary_sample_lines,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "total_python_files": len(py_files),
            "success_count": len(file_results),
            "failed_count": len(failed_files),
            "files": file_results,
            "failed_files": failed_files,
            "modules": self._group_files_by_module(file_results),
        }

        self._write_project_index(project_result)
        return project_result

    def _collect_python_files(self) -> List[str]:
        results: List[str] = []
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [
                d for d in dirs
                if d not in self.ignore_dirs and not d.startswith(".")
            ]

            for filename in files:
                if not filename.endswith(".py"):
                    continue
                abs_path = os.path.join(root, filename)
                results.append(abs_path)

        results.sort()
        return results

    def _build_wiki_file_path(self, relative_source_path: str) -> str:
        # 例如 src/foo.py -> wiki/files/src/foo.py.md
        return os.path.join(self.project_wiki_root, "files", f"{relative_source_path}.md")

    def _module_name_from_relative_path(self, relative_source_path: str) -> str:
        parts = relative_source_path.split(os.sep)
        if len(parts) <= 1:
            return "__root__"
        return parts[0]

    def _group_files_by_module(self, file_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in file_results:
            grouped[item["module"]].append(item)

        modules: List[Dict[str, Any]] = []
        for module_name in sorted(grouped.keys()):
            module_items = sorted(grouped[module_name], key=lambda x: x["file"])
            modules.append(
                {
                    "module": module_name,
                    "file_count": len(module_items),
                    "chunk_count": sum(item["chunk_count"] for item in module_items),
                    "call_relation_count": sum(item.get("call_relation_count", 0) for item in module_items),
                    "files": module_items,
                }
            )
        return modules

    def _build_module_index_path(self, module_name: str) -> str:
        safe_module_name = module_name if module_name != "__root__" else "root"
        return os.path.join(self.project_wiki_root, "modules", f"{safe_module_name}.md")

    def _build_file_wiki_link(self, wiki_file: str) -> str:
        return wiki_file.replace(os.sep, "/")

    def _write_project_index(self, project_result: Dict[str, Any]):
        json_path = os.path.join(self.project_wiki_root, "PROJECT_WIKI.json")
        md_path = os.path.join(self.project_wiki_root, "PROJECT_WIKI.md")

        with open(json_path, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(project_result, ensure_ascii=False, indent=2))

        lines: List[str] = [
            "# Project Wiki",
            "",
            f"- Project Name: {project_result['project_name']}",
            f"- Generated At: {project_result['generated_at']}",
            f"- Project Path: {project_result['project_path']}",
            f"- Wiki Root: {project_result['wiki_root']}",
            f"- Project Wiki Root: {project_result['project_wiki_root']}",
            f"- Wiki Mode: {project_result['wiki_mode']}",
            f"- Summary Max Chars: {project_result['summary_max_chars']}",
            f"- Summary Sample Lines: {project_result['summary_sample_lines']}",
            f"- Python Files: {project_result['total_python_files']}",
            f"- Success: {project_result['success_count']}",
            f"- Failed: {project_result['failed_count']}",
            "",
            "## Module Index",
            "",
        ]

        if project_result["modules"]:
            for module in project_result["modules"]:
                module_link = os.path.relpath(self._build_module_index_path(module["module"]), self.project_wiki_root)
                lines.extend(
                    [
                        f"### {module['module']}",
                        "",
                        f"- Module Wiki: [{module_link}]({module_link.replace(os.sep, '/')})",
                        f"- File Count: {module['file_count']}",
                        f"- Chunk Count: {module['chunk_count']}",
                        f"- Call Relations: {module['call_relation_count']}",
                        "",
                    ]
                )
        else:
            lines.append("(no python files indexed)")
            lines.append("")

        lines.extend(["## File Wiki Index", ""])

        if project_result["files"]:
            for item in project_result["files"]:
                wiki_link = self._build_file_wiki_link(item["wiki_file"])
                lines.extend(
                    [
                        f"### {item['file']}",
                        "",
                        f"- Module: {item['module']}",
                        f"- Wiki File: [{wiki_link}]({wiki_link})",
                        f"- Chunk Count: {item['chunk_count']}",
                        f"- Environment Range: {item['environment_range']}",
                        f"- Call Relations: {item.get('call_relation_count', 0)}",
                        "",
                    ]
                )
        else:
            lines.append("(no python files indexed)")
            lines.append("")

        lines.extend(["## Module Wiki Files", ""])

        if project_result["modules"]:
            for module in project_result["modules"]:
                module_path = self._build_module_index_path(module["module"])
                module_rel = os.path.relpath(module_path, self.project_wiki_root).replace(os.sep, "/")
                lines.append(f"- [{module['module']}]({module_rel})")
            lines.append("")

        if project_result["failed_files"]:
            lines.extend(["## Failed Files", ""])
            for item in project_result["failed_files"]:
                lines.append(f"- {item['file']}: {item['error']}")
            lines.append("")

        with open(md_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines))

        self._write_module_indexes(project_result)

    def _write_module_indexes(self, project_result: Dict[str, Any]):
        modules_root = os.path.join(self.project_wiki_root, "modules")
        os.makedirs(modules_root, exist_ok=True)

        for module in project_result["modules"]:
            module_path = self._build_module_index_path(module["module"])
            os.makedirs(os.path.dirname(module_path), exist_ok=True)

            lines: List[str] = [
                f"# Module Wiki: {module['module']}",
                "",
                f"- File Count: {module['file_count']}",
                f"- Chunk Count: {module['chunk_count']}",
                f"- Call Relations: {module['call_relation_count']}",
                "",
                "## Files",
                "",
            ]

            for item in module["files"]:
                wiki_link = self._build_file_wiki_link(item["wiki_file"])
                lines.extend(
                    [
                        f"### {item['file']}",
                        "",
                        f"- Wiki File: [{wiki_link}]({wiki_link})",
                        f"- Chunk Count: {item['chunk_count']}",
                        f"- Environment Range: {item['environment_range']}",
                        f"- Call Relations: {item.get('call_relation_count', 0)}",
                        "",
                    ]
                )

            with open(module_path, "w", encoding="utf-8") as fp:
                fp.write("\n".join(lines))