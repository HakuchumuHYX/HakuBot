import asyncio
import httpx
import json
from typing import Tuple
from nonebot.log import logger

from ..config import plugin_config
from ..models import TokenUsage

# 限制并发的 LLM 请求数量，避免 Map-Reduce + 多群任务导致瞬时并发过高
_LLM_SEMAPHORE = asyncio.Semaphore(max(1, int(plugin_config.max_concurrent_tasks or 1)))

# 可重试的 HTTP 状态码
_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# 可重试的异常类型
_RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ConnectTimeout,
    ConnectionError,
    asyncio.TimeoutError,
)


def _is_retryable_error(exc: Exception) -> bool:
    """判断异常是否可重试"""
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    # 检查是否是 HTTP 状态码错误
    if hasattr(exc, 'response') and hasattr(exc.response, 'status_code'):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    # 检查错误消息中的状态码
    err_msg = str(exc)
    for code in _RETRYABLE_STATUS_CODES:
        if f"API Error {code}" in err_msg or f"status_code={code}" in err_msg:
            return True
    return False


async def call_chat_completion(
    messages: list, 
    temperature: float = 0.5,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> Tuple[str, TokenUsage]:
    """
    调用聊天接口（带重试机制）
    
    Args:
        messages: 消息列表
        temperature: 温度参数
        max_retries: 最大重试次数（默认3次）
        base_delay: 基础延迟秒数，实际延迟为 base_delay * (2 ** attempt)
    
    Returns: (content, token_usage)

    说明：
    - total_tokens / prompt_tokens / completion_tokens 会从 OpenAI 兼容接口的 usage 字段读取
    - 如果某些兼容实现不返回 usage，将返回全 0
    - 遇到可重试错误（网络超时、429限流、5xx错误）会自动重试
    - 重试采用指数退避策略
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

    last_exception: Exception | None = None
    
    for attempt in range(max_retries):
        try:
            async with _LLM_SEMAPHORE:
                async with httpx.AsyncClient(
                    base_url=plugin_config.llm.base_url,
                    proxy=plugin_config.llm.proxy,
                    timeout=plugin_config.llm.timeout,
                ) as client:
                    resp = await client.post("/chat/completions", json=payload, headers=headers)

                if resp.status_code != 200:
                    error_msg = f"API Error {resp.status_code}: {resp.text}"
                    # 检查是否可重试
                    if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"LLM API 返回 {resp.status_code}，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})...")
                        await asyncio.sleep(delay)
                        continue
                    logger.error(f"LLM API Error: {error_msg}")
                    raise Exception(error_msg)

                data = resp.json()
                content = data["choices"][0]["message"]["content"]

                usage = data.get("usage") or {}
                token_usage = TokenUsage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )

                # 成功时如果有过重试，记录日志
                if attempt > 0:
                    logger.info(f"LLM 调用在第 {attempt + 1} 次尝试后成功")

                return content, token_usage
                
        except Exception as e:
            last_exception = e
            
            # 判断是否可重试
            if _is_retryable_error(e) and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"LLM 调用失败 ({type(e).__name__}: {e})，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})...")
                await asyncio.sleep(delay)
                continue
            
            # 不可重试或已耗尽重试次数
            if attempt == max_retries - 1:
                logger.error(f"LLM 调用在 {max_retries} 次尝试后仍失败: {e}")
            else:
                logger.error(f"LLM 调用遇到不可重试错误: {e}")
            raise
    
    # 理论上不会走到这里，但为了类型安全
    if last_exception:
        raise last_exception
    raise Exception("LLM 调用失败: 未知错误")

def fix_json(text: str) -> str:
    """尝试修复JSON字符串"""
    text = text.strip()
    
    # 处理空响应，返回空数组避免 JSON 解析错误
    if not text:
        return "[]"
    
    # 移除 markdown 代码块标记
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    
    result = text.strip()
    # 再次检查处理后是否为空
    return result if result else "[]"
