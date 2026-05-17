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
             reasoning_effort: str = None) -> str:
        """发送 Chat 请求。
        model: 模型名（如 deepseek-v4-pro）
        reasoning_effort: 思考强度，"high"/"medium"/None（None=不思考，更快）
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                kwargs = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "timeout": API_TIMEOUT,
                }
                # V4 Pro 非思考模式用 temperature 控制输出一致性
                if not reasoning_effort:
                    kwargs["temperature"] = 0.3
                else:
                    kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}

                response = self.client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"DeepSeek API 调用失败（已重试{MAX_RETRIES}次）: {last_error}")

    def validate_key(self, model: str = "deepseek-v4-pro") -> bool:
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
