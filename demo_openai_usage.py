"""
演示：从 OpenAI API 返回的结构体中取出 usage 字段
"""
from openai import OpenAI

client = OpenAI()

# 模拟一次 API 调用（实际使用时请替换为真实请求）
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello"}]
)

# 方法1：直接通过属性访问 usage 对象
usage = response.usage
print(f"Prompt tokens: {usage.prompt_tokens}")
print(f"Completion tokens: {usage.completion_tokens}")
print(f"Total tokens: {usage.total_tokens}")

# 方法2：转为字典（Pydantic v2 使用 model_dump）
usage_dict = usage.model_dump()
print(f"Usage dict: {usage_dict}")

# 方法3：直接取 total_tokens（最常用）
print(f"Total tokens (direct): {response.usage.total_tokens}")
