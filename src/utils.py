import json
import re


def safe_json_parse(text: str) -> dict:
    """安全解析 JSON，支持多种格式回退"""
    if not text:
        return {}

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 代码块
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取 ``` ... ``` 代码块
    match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { 到最后一个 }
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


def extract_json_array(text: str) -> list:
    """安全提取 JSON 数组"""
    result = safe_json_parse(text)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        # 尝试从 dict 中提取第一个数组值
        for val in result.values():
            if isinstance(val, list):
                return val
    return []


def truncate_text(text: str, max_length: int = 800) -> str:
    """截断文本到指定长度"""
    if not text or len(text) <= max_length:
        return text or ""
    return text[:max_length] + "..."
