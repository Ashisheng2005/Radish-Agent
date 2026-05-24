from openai import OpenAI

class DeepSeek():
    def __init__(self, api_key=None, base_url=None, 
                 model=None,
                 language=None,
                 debug=False):
        self.api_key = api_key
        self.language = language
        self.base_url = base_url 
        self.model = model
        self.debug = bool(debug)
        # self.history_limit = max(0, history_limit)
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url

        )
        
    def sendinfo(self, messages, tools=None, tool_choice=None, temperature=0.7, max_tokens=4000):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
        tool_calls = message.tool_calls if hasattr(message, "tool_calls") and message.tool_calls else None
        usage = response.usage if hasattr(response, "usage") else None
        usage_dict = usage.model_dump() if usage else None
        if usage_dict:
            usage_dict = self._normalize_usage_dict(usage_dict)

        if self.debug:
            finish_reason = choice.finish_reason
            print(f"[deepseek.debug] finish_reason={finish_reason}")
            print(f"[deepseek.debug] content_repr={repr(content)}")
            if tool_calls:
                print(f"[deepseek.debug] tool_calls={[(tc.function.name, tc.function.arguments) for tc in tool_calls]}")
            if usage is not None:
                print(f"[deepseek.debug] usage={usage}")

        return content.strip(), tool_calls, usage_dict

    @staticmethod
    def _normalize_usage_dict(usage_dict: dict) -> dict:
        """展平各 provider 的 cache 字段，便于 Polling 统计 cache 命中率。"""
        out = dict(usage_dict)
        cached = int(out.get("cached_tokens", 0) or 0)
        details = out.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = max(cached, int(details.get("cached_tokens", 0) or 0))
        # DeepSeek 官方字段：prompt_cache_hit_tokens（非 OpenAI 的 cached_tokens）
        cached = max(cached, int(out.get("prompt_cache_hit_tokens", 0) or 0))
        if cached:
            out["cached_tokens"] = cached
        return out
