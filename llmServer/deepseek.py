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
        
    def sendinfo(self, messages, temperature=0.7, max_tokens=4000):
        # print(f"Sending messages to DeepSeek API: {messages}")
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            extra_body={"thinking": {"type": "disabled"}}
        )
        # print(f"DeepSeek API response: {response}")
        # content = (response.choices[0].message.content or "")
        content = response.choices[0].message.content
        # 统计token用量
        # usage = getattr(response, "usage", None)
        usage = response.usage if hasattr(response, "usage") else None
        usage_dict = usage.model_dump() if usage else None

        if self.debug:
            finish_reason = response.choices[0].finish_reason
            print(f"[deepseek.debug] finish_reason={finish_reason}")
            print(f"[deepseek.debug] content_repr={repr(content)}")
            if usage is not None:
                print(f"[deepseek.debug] usage={usage}")

        return content.strip(), usage_dict
    
    