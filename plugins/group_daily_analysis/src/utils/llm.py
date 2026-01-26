import httpx
import json
from typing import List, Tuple, Any
from nonebot.log import logger
from ..config import plugin_config

async def call_chat_completion(messages: list) -> Tuple[str, int]:
    """
    调用聊天接口
    Returns: (content, total_tokens)
    """
    headers = {
        "Authorization": f"Bearer {plugin_config.llm.api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": plugin_config.llm.model,
        "messages": messages,
        "max_tokens": 4096  # Reasonable default
    }

    try:
        async with httpx.AsyncClient(
                base_url=plugin_config.llm.base_url,
                proxy=plugin_config.llm.proxy,
                timeout=plugin_config.llm.timeout
        ) as client:
            resp = await client.post("/chat/completions", json=payload, headers=headers)

            if resp.status_code != 200:
                logger.error(f"LLM API Error {resp.status_code}: {resp.text}")
                raise Exception(f"API Error {resp.status_code}: {resp.text}")

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            total_tokens = data.get("usage", {}).get("total_tokens", 0)

            return content, total_tokens
    except Exception as e:
        logger.error(f"LLM调用失败: {e}")
        raise

def fix_json(text: str) -> str:
    """尝试修复JSON字符串"""
    text = text.strip()
    # 移除 markdown 代码块标记
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()
