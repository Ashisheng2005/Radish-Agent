"""集成验收：确认 provider usage 是否返回 cache 命中 token，并观测命中率。

需要 config.yaml 中已配置可用的 API_KEY / BASE_URL / MODEL。

用法（仓库根目录）:
  python tests/integration/verify_prompt_cache_usage.py
  python tests/integration/verify_prompt_cache_usage.py --rounds 3

通过条件（启发式）:
  - 第 1 轮能拿到 usage（prompt_tokens > 0）
  - 第 2+ 轮若 provider 支持前缀缓存，cached_tokens 或 prompt_cache_hit_tokens > 0
  - 若多轮均为 0，脚本仍 exit 0 但打印 WARN（可能是前缀过短、provider 不支持、或 MiMo 等未暴露字段）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_SERVER = REPO_ROOT / "llmServer"
sys.path.insert(0, str(LLM_SERVER))

from deepseek import DeepSeek
from llmPolling import Polling
from yamlConfig import Config


def _extract_cache_raw(usage: dict | None) -> dict:
    u = usage or {}
    details = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"), dict) else {}
    return {
        "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
        "cached_tokens": int(u.get("cached_tokens", 0) or 0),
        "prompt_cache_hit_tokens": int(u.get("prompt_cache_hit_tokens", 0) or 0),
        "prompt_cache_miss_tokens": int(u.get("prompt_cache_miss_tokens", 0) or 0),
        "details_cached_tokens": int(details.get("cached_tokens", 0) or 0),
    }


def _run_direct_client(client: DeepSeek, rounds: int) -> list[dict]:
    """固定 system 前缀，连续请求观测 cache 字段。"""
    system = (
        "You are a cache probe assistant. Reply with exactly one short sentence. "
        "Do not use tools. " * 40
    )
    results = []
    for i in range(1, rounds + 1):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"[Task]\nProbe round {i}: say OK."},
        ]
        _, _, usage = client.sendinfo(messages, temperature=0, max_tokens=32)
        row = {"round": i, "usage_raw": usage, "cache": _extract_cache_raw(usage)}
        row["polling_fields"] = Polling._usage_cache_fields(usage)
        results.append(row)
        print(f"\n--- direct API round {i} ---")
        print(json.dumps(row, ensure_ascii=False, indent=2))
    return results


def _run_polling_sendinfo(bot: Polling, rounds: int) -> list[dict]:
    """经 Polling.sendinfo（含工具链路径）观测 metrics_rounds。"""
    results = []
    for i in range(1, rounds + 1):
        bot.metrics_rounds.clear()
        prompt = f"第 {i} 轮：用一句话回答“收到”。不要调用任何工具。"
        bot.sendinfo(prompt, temperature=0, max_tokens=64, max_tool_rounds=1)
        last = bot.metrics_rounds[-1] if bot.metrics_rounds else {}
        row = {
            "round": i,
            "metrics_round": last,
            "cache_hit_rate": last.get("cache_hit_rate"),
            "cached_tokens": last.get("cached_tokens"),
        }
        results.append(row)
        print(f"\n--- Polling.sendinfo round {i} ---")
        print(json.dumps(row, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify prompt cache usage fields from provider")
    parser.add_argument("--rounds", type=int, default=2, help="number of identical-prefix rounds")
    parser.add_argument("--skip-polling", action="store_true", help="only run direct client probe")
    args = parser.parse_args()

    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        print("FAIL: config.yaml not found")
        return 1

    cfg = Config(config_path)
    provider = cfg.get_nested("MODEL_SELECT", "model_name")
    api_key = cfg.get_nested(provider, "API_KEY")
    base_url = cfg.get_nested(provider, "BASE_URL", default="https://api.deepseek.com/v1")
    model = cfg.get_nested(provider, "MODEL", default="deepseek-chat")

    if not api_key:
        print("FAIL: API_KEY missing in config.yaml")
        return 1

    print(f"Provider: {provider}  model: {model}  base_url: {base_url}")

    client = DeepSeek(api_key=api_key, base_url=base_url, model=model, debug=False)
    direct = _run_direct_client(client, max(2, args.rounds))

    polling_rows = []
    if not args.skip_polling:
        bot = Polling(verbose=True)
        bot.set_show_usage(True)
        bot.clear_context()
        polling_rows = _run_polling_sendinfo(bot, max(2, args.rounds))

    # 判定
    has_usage = any(r["cache"]["prompt_tokens"] > 0 for r in direct)
    hit_seen = any(
        r["cache"]["cached_tokens"] > 0
        or r["cache"]["prompt_cache_hit_tokens"] > 0
        or r["cache"]["details_cached_tokens"] > 0
        for r in direct[1:]
    )
    polling_hit = any((r.get("cached_tokens") or 0) > 0 for r in polling_rows[1:]) if polling_rows else False

    print("\n=== Summary ===")
    print("usage returned:", has_usage)
    print("cache hit seen (round 2+):", hit_seen or polling_hit)
    if has_usage and not (hit_seen or polling_hit):
        print(
            "WARN: usage 有返回，但第 2 轮起未见 cache 命中字段。"
            "可能原因：前缀 <64 token、provider 未开启/未暴露 cache、或字段名未映射。"
            "请检查 usage 原始 JSON 是否含 prompt_cache_hit_tokens 或 prompt_tokens_details.cached_tokens。"
        )
        return 0
    if not has_usage:
        print("FAIL: 未拿到 usage")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
