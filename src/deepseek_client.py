import time
from openai import OpenAI
from .config import DEEPSEEK_BASE_URL, API_TIMEOUT, MAX_RETRIES


class DeepSeekClient:
    """DeepSeek API 客户端，适配 V4 Pro"""

    def __init__(self, api_key: str):
        self.client = OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
        )

    def chat(self, system_prompt: str, user_prompt: str,
             model: str, max_tokens: int = 8192,
             temperature: float = 0.3) -> str:
        """发送 Chat 请求"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=API_TIMEOUT,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"API 调用失败（重试{MAX_RETRIES}次）: {last_error}")

    def validate_key(self, model: str = "deepseek-v4-flash") -> bool:
        try:
            self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
                timeout=30,
            )
            return True
        except Exception:
            return False
