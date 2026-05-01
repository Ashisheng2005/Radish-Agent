import sys
import yaml
from pathlib import Path
from openai import OpenAI


KNOWN_PROVIDERS = [
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("OpenAI", "https://api.openai.com/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("Together AI", "https://api.together.xyz/v1"),
    ("MiMo", "https://token-plan-cn.xiaomimimo.com/v1"),
]


# 常见模型的上下文窗口大小映射表（第一优先级）
# 精确匹配 model id；对后缀版本号用 startswith 前缀匹配
MODEL_CONTEXT_MAP = {
    "deepseek-chat": 65536,
    "deepseek-reasoner": 65536,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-3.5-turbo": 16384,
    "o1": 200000,
    "o3-mini": 200000,
    "claude-3-5-sonnet-*": 200000,
    "claude-3-opus-*": 200000,
    "claude-3-haiku-*": 200000,
    "claude-4-*": 200000,
    "gemini-*": 1048576,
    "llama-*-8b-*": 131072,
    "llama-*-70b-*": 131072,
    "llama-*-405b-*": 131072,
    "mixtral-*": 32768,
    "qwen-*": 131072,
}

# models.dev 本地缓存路径
MODELS_CACHE_FILE = Path(__file__).parent / "models_dev_cache.json"

# 提供商名称 → models.dev provider slug 映射（名称不一致时使用）
PROVIDER_SLUG_MAP = {
    "MiMo": "xiaomi",
}


def _print_header(text: str):
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}")


def _choose_provider():
    _print_header("选择 LLM 提供商")
    for i, (name, url) in enumerate(KNOWN_PROVIDERS, 1):
        print(f"  {i}. {name} ({url})")
    print(f"  {len(KNOWN_PROVIDERS) + 1}. 自定义 URL")

    while True:
        try:
            choice = input(f"\n请输入编号 (1-{len(KNOWN_PROVIDERS) + 1}): ").strip()
            idx = int(choice)
            if 1 <= idx <= len(KNOWN_PROVIDERS):
                return KNOWN_PROVIDERS[idx - 1]
            elif idx == len(KNOWN_PROVIDERS) + 1:
                url = input("请输入 API Base URL (例如 https://api.example.com/v1): ").strip()
                if url:
                    name = input("请给这个提供商起个名称 (用于 config.yaml 的标识): ").strip()
                    if name:
                        return (name, url)
                    print("名称不能为空。")
                else:
                    print("URL 不能为空。")
            else:
                print(f"请输入 1-{len(KNOWN_PROVIDERS) + 1} 之间的数字。")
        except ValueError:
            print("请输入有效数字。")


def _input_api_key(provider_name: str):
    _print_header("输入 API 密钥")
    print(f"  提供商: {provider_name}")
    api_key = input("  API Key: ").strip()
    return api_key


def fetch_models(base_url: str, api_key: str):
    """调用 OpenAI 兼容的 model list API 获取可用模型列表。"""
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        models = client.models.list()
        ids = sorted([m.id for m in models])
        return ids
    except Exception as e:
        print(f"\n  拉取模型列表失败: {e}")
        return None


def _choose_model(provider_name: str, base_url: str, api_key: str):
    _print_header(f"选择模型 ({provider_name})")

    models = fetch_models(base_url, api_key)

    if models:
        print(f"  共获取到 {len(models)} 个模型:\n")
        for i, m in enumerate(models, 1):
            print(f"  {i}. {m}")

        while True:
            choice = input(f"\n  请输入编号 (1-{len(models)}) 或直接输入模型 ID: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(models):
                    return models[idx]
            except ValueError:
                pass
            if choice:
                return choice
    else:
        return input("\n  无法拉取模型列表，请手动输入模型名称 (例如 gpt-4o): ").strip()


def _load_models_cache() -> dict | None:
    """从本地缓存加载 models.dev 数据，不存在时返回 None。"""
    if MODELS_CACHE_FILE.exists():
        import json
        with open(MODELS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_models_cache(data: dict):
    """将 models.dev 数据保存到本地缓存。"""
    import json
    MODELS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MODELS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  模型数据库已缓存到: {MODELS_CACHE_FILE}")


def refresh_models_cache() -> bool:
    """重新下载 models.dev 数据并保存到本地缓存。"""
    import requests
    print("正在刷新模型数据库...")
    try:
        resp = requests.get("https://models.dev/api.json", timeout=30)
        if resp.status_code != 200:
            print(f"  刷新失败: HTTP {resp.status_code}")
            return False
        data = resp.json()
        _save_models_cache(data)
        model_count = sum(len(p.get("models", {})) for p in data.values() if isinstance(p, dict))
        print(f"  刷新完成，共 {model_count} 个模型")
        return True
    except Exception as e:
        print(f"  刷新异常: {e}")
        return False


def _search_models_cache(model: str, provider_name: str, data: dict) -> int | None:
    """在 models.dev 数据中搜索模型上下文大小。

    数据结构：{provider_slug: {..., "models": {model_id: {..., "limit": {"context": N}}}}}
    """
    # 提供商名称 → models.dev slug 映射
    target_slug = PROVIDER_SLUG_MAP.get(provider_name, provider_name).lower()
    for slug, provider_data in data.items():
        if not isinstance(provider_data, dict):
            continue
        if slug.lower() != target_slug:
            continue
        models_dict = provider_data.get("models", {})
        if not isinstance(models_dict, dict):
            continue
        for mid, model_data in models_dict.items():
            if not isinstance(model_data, dict):
                continue
            if mid != model:
                continue
            limit = model_data.get("limit", {})
            if isinstance(limit, dict):
                ctx = limit.get("context")
                if ctx:
                    return int(ctx)
    return None


def _fetch_context_window(model: str, provider_name: str, base_url: str, api_key: str) -> int:
    """获取模型的上下文窗口大小，三阶降级：硬编码映射 → models.dev 缓存/API → 用户手动输入。"""
    import fnmatch

    # 第一级：精确匹配 model id
    if model in MODEL_CONTEXT_MAP:
        print(f"  模型上下文: {model} → {MODEL_CONTEXT_MAP[model]} tokens")
        return MODEL_CONTEXT_MAP[model]

    # 第一级续：通配符匹配
    for pattern, ctx in MODEL_CONTEXT_MAP.items():
        if "*" not in pattern:
            continue
        if fnmatch.fnmatch(model, pattern):
            print(f"  模型上下文: {model} → {ctx} tokens (匹配模式: {pattern})")
            return ctx

    # 第二级：查询 models.dev（本地缓存 → 远程 API）
    cache_data = _load_models_cache()
    if cache_data is None:
        print("  首次使用，正在下载模型数据库...")
        import requests
        try:
            resp = requests.get("https://models.dev/api.json", timeout=30)
            if resp.status_code == 200:
                cache_data = resp.json()
                _save_models_cache(cache_data)
            else:
                print(f"  下载失败: HTTP {resp.status_code}")
        except Exception as e:
            print(f"  下载异常: {e}")

    if cache_data is not None:
        ctx = _search_models_cache(model, provider_name, cache_data)
        if ctx:
            print(f"  从 models.dev 获取: {model} → {ctx} tokens")
            return ctx
        print(f"  models.dev 未找到匹配 (model={model}, provider={provider_name})")
    else:
        print("  models.dev 缓存不可用")

    # 第三级：用户手动输入
    print(f"\n  未能自动获取「{model}」的上下文大小。")
    user_input = input(f"  请手动输入上下文窗口大小（默认 128000）: ").strip()
    if user_input.isdigit():
        return int(user_input)
    return 128000


def write_config(config_path: str, provider_name: str, base_url: str, api_key: str, model: str, context_window: int = None):
    """将提供商配置持久化到 config.yaml。"""
    path = Path(config_path)

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    block = {
        "API_KEY": api_key,
        "BASE_URL": base_url,
        "MAX_TOKEN": context_window or data.get(provider_name, {}).get("MAX_TOKEN", 128000),
        "MODEL": model,
        "LANGUAGE": data.get(provider_name, {}).get("LANGUAGE", "Chinese"),
    }
    data[provider_name] = block
    data["MODEL_SELECT"] = {"model_name": provider_name}

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"\n  配置已写入: {path.resolve()}")


def run_setup(config_path: str):
    """交互式配置向导主入口。"""
    name, url = _choose_provider()
    api_key = _input_api_key(name)
    if not api_key:
        print("API Key 不能为空，配置取消。")
        return
    model = _choose_model(name, url, api_key)
    if not model:
        print("模型名称不能为空，配置取消。")
        return

    context_window = _fetch_context_window(model, name, url, api_key)

    _print_header("确认配置")
    print(f"  提供商: {name}")
    print(f"  Base URL: {url}")
    print(f"  模型: {model}")
    print(f"  上下文窗口: {context_window} tokens")
    confirm = input("\n  确认写入 config.yaml? (y/N): ").strip().lower()
    if confirm == "y":
        write_config(config_path, name, url, api_key, model, context_window)
        print("  配置完成。请重启程序使新配置生效。")
    else:
        print("  已取消，配置未写入。")


if __name__ == "__main__":
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    run_setup(str(config_path))
