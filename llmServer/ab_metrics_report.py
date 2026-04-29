import json
from pathlib import Path
from statistics import mean


def load_events(path: Path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize(events):
    if not events:
        return {}
    metric_events = [x for x in events if x.get("event") in {"chat_complete", "tool_round_limit"}]
    if not metric_events:
        metric_events = events
    samples = len(metric_events)
    # tokens 汇总
    total_prompt_tokens = sum((x.get("tokens") or {}).get("prompt_tokens", 0) for x in metric_events)
    total_completion_tokens = sum((x.get("tokens") or {}).get("completion_tokens", 0) for x in metric_events)
    total_tokens = sum((x.get("tokens") or {}).get("total_tokens", 0) for x in metric_events)

    return {
        "samples": samples,
        "avg_tool_round_count": round(mean(x.get("tool_round_count", 0) for x in metric_events), 3),
        "avg_tool_calls": round(mean(x.get("tool_calls", 0) for x in metric_events), 3),
        "avg_reply_chars": round(mean(x.get("reply_chars", 0) for x in metric_events), 3),
        "avg_tool_result_chars": round(mean(x.get("avg_tool_result_chars", 0) for x in metric_events), 3),
        "duplicate_tool_call_rate": round(mean(x.get("duplicate_tool_call_rate", 0) for x in metric_events), 4),
        "format_compliance_rate": round(mean(x.get("format_compliance_rate", 0) for x in metric_events), 4),
        "total_prompt_tokens": int(total_prompt_tokens),
        "total_completion_tokens": int(total_completion_tokens),
        "total_tokens": int(total_tokens),
        "avg_total_tokens": round(total_tokens / samples, 3) if samples else 0.0,
    }



def main():
    base = Path(__file__).resolve().parents[1]
    a_path = base / "runtime_metrics_a.jsonl"
    b_path = base / "runtime_metrics_b.jsonl"
    default_path = base / "runtime_metrics.jsonl"

    a_events = load_events(a_path)
    b_events = load_events(b_path)

    if not a_events and not b_events:
        default_events = load_events(default_path)
        print("Default summary:", summarize(default_events))
        return

    print("A summary:", summarize(a_events))
    print("B summary:", summarize(b_events))


if __name__ == "__main__":
    main()
