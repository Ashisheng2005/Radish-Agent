import argparse
import io
import json
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

from llmPolling import Polling


def load_cases(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"case 配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("case 配置格式错误，顶层必须是对象")
    return data


def list_cases(cases: dict):
    for name, item in cases.items():
        desc = str(item.get("description", "")).strip()
        group = str(item.get("group", "default")).strip() or "default"
        print(f"- [{group}] {name}: {desc}")


def select_cases_by_group(cases: dict, groups_text: str):
    names = [x.strip() for x in str(groups_text or "").split(",") if x.strip()]
    wanted = set(names)
    selected = []
    for case_name, cfg in cases.items():
        group = str(cfg.get("group", "default")).strip() or "default"
        if group in wanted:
            selected.append((case_name, cfg))
    return selected


def parse_tool_names_from_logs(log_text: str):
    if not log_text:
        return []
    names = []
    patterns = [
        re.compile(r"工具结果:\s*([a-zA-Z_][\w]*)\s*->"),
        re.compile(r"tools:\s*([a-zA-Z_][\w]*)"),
    ]
    for pattern in patterns:
        names.extend(pattern.findall(log_text))
    return names


def parse_error_types(text: str):
    if not text:
        return []
    pattern = re.compile(r"error_type['\"]?\s*[:=]\s*['\"]([a-zA-Z_][\w]*)['\"]")
    return pattern.findall(text)


def resolve_prompt(prompt: str, project_root: Path):
    return str(prompt or "").replace("${PROJECT_ROOT}", str(project_root).replace("\\", "/"))


def extract_latest_metrics(metrics_path: Path, started_at: float):
    if not metrics_path.exists():
        return {}
    latest = {}
    try:
        lines = metrics_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 只拿本次运行期间的新事件，避免历史污染
        event_time = payload.get("ts", None)
        if event_time is not None and isinstance(event_time, (int, float)) and event_time < started_at:
            continue
        latest = payload
        break
    return latest


def validate_run(
    case_name: str,
    cfg: dict,
    tool_names: list,
    latency_ms: int,
    final_reply: str,
    metrics: dict,
    log_text: str,
):
    failures = []
    expected_tools = cfg.get("expected_tools", [])
    max_tool_calls = int(cfg.get("max_tool_calls", 999))
    min_tool_calls = int(cfg.get("min_tool_calls", 0))
    max_latency_ms = int(cfg.get("max_latency_ms", 999999))
    require_three = bool(cfg.get("require_three_section_output", True))
    expected_error_type = str(cfg.get("expect_error_type", "")).strip()
    expect_warning_contains = str(cfg.get("expect_warning_contains", "")).strip()
    expect_intent_mode = str(cfg.get("expect_intent_mode", "")).strip()

    called_set = set(tool_names)
    for tool in expected_tools:
        if tool not in called_set:
            failures.append(f"缺少预期工具调用: {tool}")

    metric_tool_calls = metrics.get("tool_calls", len(tool_names))
    if metric_tool_calls > max_tool_calls:
        failures.append(f"工具调用次数超限: {metric_tool_calls} > {max_tool_calls}")
    if metric_tool_calls < min_tool_calls:
        failures.append(f"工具调用次数不足: {metric_tool_calls} < {min_tool_calls}")

    if latency_ms > max_latency_ms:
        failures.append(f"耗时超限: {latency_ms}ms > {max_latency_ms}ms")

    observed_error_types = set(parse_error_types(log_text) + parse_error_types(final_reply))
    if expected_error_type and expected_error_type not in observed_error_types:
        failures.append(
            f"未观察到期望错误类型: {expected_error_type}，实际={sorted(observed_error_types) or ['(none)']}"
        )

    if expect_warning_contains:
        merged_text = f"{log_text}\n{final_reply}"
        if expect_warning_contains not in merged_text:
            failures.append(f"未观察到期望 warning 文本: {expect_warning_contains}")

    if expect_intent_mode:
        intent_mode = str(metrics.get("intent_mode", "")).strip()
        if intent_mode != expect_intent_mode:
            failures.append(f"intent_mode 不匹配: {intent_mode} != {expect_intent_mode}")

    if require_three:
        text = str(final_reply or "")
        ok_structured = (
            text.startswith("Conclusion:")
            and "\nEvidence:" in text
            and "\nNextStep:" in text
        )
        ok_natural = ("依据是：" in text and "下一步建议：" in text)
        ok_format = ok_structured or ok_natural
        if not ok_format:
            failures.append("输出不满足三段式格式（Conclusion/Evidence/NextStep）")

    return {
        "case": case_name,
        "pass": len(failures) == 0,
        "failures": failures,
        "latency_ms": latency_ms,
        "tool_calls_in_logs": len(tool_names),
        "tool_calls_metric": metric_tool_calls,
        "called_tools": tool_names,
        "observed_error_types": sorted(observed_error_types),
        "intent_mode": metrics.get("intent_mode"),
        "final_reply": final_reply,
    }


def run_case(case_name: str, cfg: dict, metrics_path: Path, retry: int, project_root: Path):
    prompt = resolve_prompt(cfg.get("prompt", ""), project_root)
    if not prompt:
        raise ValueError(f"case {case_name} 缺少 prompt")

    last_result = None
    for idx in range(retry + 1):
        polling = Polling(debug=bool(cfg.get("debug", False)), verbose=bool(cfg.get("verbose", False)))
        started = time.time()
        log_buffer = io.StringIO()
        with redirect_stdout(log_buffer):
            reply, usage_dict = polling.sendinfo(prompt, temperature=0, max_tokens=int(cfg.get("max_tokens", 1200)))
        ended = time.time()

        log_text = log_buffer.getvalue()
        tool_names = parse_tool_names_from_logs(log_text)
        metrics = extract_latest_metrics(metrics_path, started)
        result = validate_run(
            case_name=case_name,
            cfg=cfg,
            tool_names=tool_names,
            latency_ms=int((ended - started) * 1000),
            final_reply=reply,
            metrics=metrics,
            log_text=log_text,
        )
        result["attempt"] = idx + 1
        result["log_excerpt"] = log_text[-1600:]
        last_result = result
        if result["pass"]:
            return result
    return last_result


def build_parser():
    parser = argparse.ArgumentParser(description="llmPolling 测试流程 CLI")
    default_cases = Path(__file__).resolve().with_name("test_cases.json")
    parser.add_argument("--cases-file", default=str(default_cases), help="测试用例配置路径")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]), help="项目根目录")
    parser.add_argument("--metrics-file", default="", help="指标文件路径，默认使用项目根目录下 runtime_metrics.jsonl")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list-cases", help="列出可用测试用例")
    list_cmd.set_defaults(command="list-cases")

    run_cmd = sub.add_parser("run", help="运行测试用例")
    run_cmd.add_argument("--case", dest="case_name", help="case 名称")
    run_cmd.add_argument("--group", help="按组运行，支持逗号分隔，如 smoke,regression")
    run_cmd.add_argument("--prompt", help="直接运行自定义 prompt")
    run_cmd.add_argument("--max-tool-calls", type=int, default=6, help="自定义 prompt 时工具调用上限")
    run_cmd.add_argument("--max-latency-ms", type=int, default=30000, help="自定义 prompt 时耗时上限")
    run_cmd.add_argument("--retry", type=int, default=0, help="失败重试次数")
    run_cmd.add_argument("--json", action="store_true", help="JSON 输出结果")
    run_cmd.set_defaults(command="run")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    cases = load_cases(Path(args.cases_file))
    project_root = Path(args.project_root).resolve()
    metrics_path = Path(args.metrics_file).resolve() if args.metrics_file else (project_root / "runtime_metrics.jsonl")

    if args.command == "list-cases":
        list_cases(cases)
        return

    if args.prompt:
        case_name = "adhoc"
        cfg = {
            "description": "ad-hoc prompt",
            "prompt": args.prompt,
            "expected_tools": [],
            "max_tool_calls": args.max_tool_calls,
            "max_latency_ms": args.max_latency_ms,
            "require_three_section_output": True,
        }
        result = run_case(
            case_name=case_name,
            cfg=cfg,
            metrics_path=metrics_path,
            retry=max(0, args.retry),
            project_root=project_root,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        print(f"Case: {result['case']} (attempt {result.get('attempt', 1)})")
        print(f"Latency: {result['latency_ms']}ms")
        print(f"ToolCalls(log/metric): {result['tool_calls_in_logs']}/{result['tool_calls_metric']}")
        print(f"IntentMode(metric): {result.get('intent_mode')}")
        print(f"ErrorTypes: {', '.join(result.get('observed_error_types', [])) or '(none)'}")
        print(f"Status: {'PASS' if result['pass'] else 'FAIL'}")
        if result["failures"]:
            print("Failures:")
            for item in result["failures"]:
                print(f"- {item}")
        print("--- Final Reply ---")
        print(result["final_reply"])

        if not result["pass"]:
            sys.exit(2)
        return
    else:
        if args.group:
            selected_cases = select_cases_by_group(cases, args.group)
            if not selected_cases:
                parser.error(f"未找到 group: {args.group}")

            results = []
            for case_name, cfg in selected_cases:
                result = run_case(
                    case_name=case_name,
                    cfg=cfg,
                    metrics_path=metrics_path,
                    retry=max(0, args.retry),
                    project_root=project_root,
                )
                results.append(result)
                print(f"Case: {result['case']} | Status: {'PASS' if result['pass'] else 'FAIL'} | Latency: {result['latency_ms']}ms")
                if result["failures"]:
                    for item in result["failures"]:
                        print(f"- {item}")

            summary = {
                "total": len(results),
                "passed": sum(1 for x in results if x.get("pass")),
                "failed": sum(1 for x in results if not x.get("pass")),
                "results": results,
            }
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                print("=== Summary ===")
                print(f"Total: {summary['total']}  Passed: {summary['passed']}  Failed: {summary['failed']}")

            if summary["failed"] > 0:
                sys.exit(2)
            return

        case_name = args.case_name
        if not case_name:
            parser.error("run 模式必须传 --case 或 --group 或 --prompt")
        if case_name not in cases:
            parser.error(f"未找到 case: {case_name}")
        cfg = cases[case_name]
        result = run_case(
            case_name=case_name,
            cfg=cfg,
            metrics_path=metrics_path,
            retry=max(0, args.retry),
            project_root=project_root,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        print(f"Case: {result['case']} (attempt {result.get('attempt', 1)})")
        print(f"Latency: {result['latency_ms']}ms")
        print(f"ToolCalls(log/metric): {result['tool_calls_in_logs']}/{result['tool_calls_metric']}")
        print(f"IntentMode(metric): {result.get('intent_mode')}")
        print(f"ErrorTypes: {', '.join(result.get('observed_error_types', [])) or '(none)'}")
        print(f"Status: {'PASS' if result['pass'] else 'FAIL'}")
        if result["failures"]:
            print("Failures:")
            for item in result["failures"]:
                print(f"- {item}")
        print("--- Final Reply ---")
        print(result["final_reply"])

        if not result["pass"]:
            sys.exit(2)


if __name__ == "__main__":
    main()
