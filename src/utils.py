import json
import re


def safe_json_parse(text: str) -> dict:
    """安全解析 JSON，支持多种格式回退"""
    if not text:
        return {}

    # 先清理 DeepSeek thinking 模型可能包裹的标签
    cleaned = text
    cleaned = re.sub(r'<thinking>.*?</thinking>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<reasoning>.*?</reasoning>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<thought>.*?</thought>', '', cleaned, flags=re.DOTALL)

    # 尝试直接解析
    for candidate in [cleaned, text]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 提取 ```json ... ```
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 提取 ``` ... ```
    match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 提取第一个 { 到最后一个 }
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


def extract_json_array(text: str) -> list:
    result = safe_json_parse(text)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for val in result.values():
            if isinstance(val, list):
                return val
    return []


def truncate_text(text: str, max_length: int = 800) -> str:
    if not text or len(text) <= max_length:
        return text or ""
    return text[:max_length] + "..."
