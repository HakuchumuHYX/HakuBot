import asyncio
import httpx
import json
from typing import Tuple
from nonebot.log import logger

from ..config import plugin_config
from ..models import TokenUsage

# 限制并发的 LLM 请求数量，避免 Map-Reduce + 多群任务导致瞬时并发过高
_LLM_SEMAPHORE = asyncio.Semaphore(max(1, int(plugin_config.max_concurrent_tasks or 1)))


async def call_chat_completion(messages: list, temperature: float = 0.5) -> Tuple[str, TokenUsage]:
    """
    调用聊天接口
    Returns: (content, token_usage)

    说明：
    - total_tokens / prompt_tokens / completion_tokens 会从 OpenAI 兼容接口的 usage 字段读取
    - 如果某些兼容实现不返回 usage，将返回全 0
    """
    headers = {
        "Authorization": f"Bearer {plugin_config.llm.api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": plugin_config.llm.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,  # Reasonable default
    }

    try:
        async with _LLM_SEMAPHORE:
            async with httpx.AsyncClient(
                base_url=plugin_config.llm.base_url,
                proxy=plugin_config.llm.proxy,
                timeout=plugin_config.llm.timeout,
            ) as client:
                resp = await client.post("/chat/completions", json=payload, headers=headers)

            if resp.status_code != 200:
                logger.error(f"LLM API Error {resp.status_code}: {resp.text}")
                raise Exception(f"API Error {resp.status_code}: {resp.text}")

            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            usage = data.get("usage") or {}
            token_usage = TokenUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            )

            return content, token_usage
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
