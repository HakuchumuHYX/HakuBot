import asyncio
import json
from json_repair import repair_json
from typing import Tuple
from nonebot.log import logger
from plugins.utils.llm import (
    LLMClientConfig,
    chat_completion as shared_chat_completion,
    is_retryable_llm_error as _is_retryable_error,
)

from ..config import plugin_config
from ..models import TokenUsage

# 限制并发的 LLM 请求数量，避免 Map-Reduce + 多群任务导致瞬时并发过高
_LLM_SEMAPHORE = asyncio.Semaphore(max(1, int(plugin_config.max_concurrent_tasks or 1)))

async def call_chat_completion(
    messages: list,
    temperature: float = 0.5,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> Tuple[str, TokenUsage]:
    """
    调用统一聊天后端（带重试机制），返回 (content, token_usage)。
    """
    llm_config = LLMClientConfig(
        provider=plugin_config.llm.provider,
        api_key=plugin_config.llm.api_key,
        base_url=plugin_config.llm.base_url,
        model=plugin_config.llm.model,
        timeout=plugin_config.llm.timeout,
        proxy=plugin_config.llm.proxy,
        max_tokens=plugin_config.llm.max_tokens,
        thinking_enabled=plugin_config.llm.thinking_enabled,
        reasoning_effort=plugin_config.llm.reasoning_effort,
        extra_body=plugin_config.llm.extra_body,
    )
    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with _LLM_SEMAPHORE:
                result = await shared_chat_completion(
                    llm_config,
                    messages,
                    temperature=temperature,
                )

            token_usage = TokenUsage(
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                total_tokens=result.usage.total_tokens,
            )

            if attempt > 0:
                logger.info(f"LLM 调用在第 {attempt + 1} 次尝试后成功")

            return result.content, token_usage

        except Exception as e:
            last_exception = e

            if _is_retryable_error(e) and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"LLM 调用失败 ({type(e).__name__}: {e})，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})..."
                )
                await asyncio.sleep(delay)
                continue

            if attempt == max_retries - 1:
                logger.error(f"LLM 调用在 {max_retries} 次尝试后仍失败: {e}")
            else:
                logger.error(f"LLM 调用遇到不可重试错误: {e}")
            raise

    if last_exception:
        raise last_exception
    raise Exception("LLM 调用失败: 未知错误")

def fix_json(text: str) -> str:
    """
    尝试从模型输出中"提取并修复"JSON字符串。

    目标：
    - 允许模型输出解释性文字 + JSON，我们尽量把 JSON 抠出来
    - 支持 ```json ... ``` / ``` ... ``` 代码块
    - 支持提取最外层 JSON 数组 `[...]` 或对象 `{...}`
    - 使用 json_repair 修复常见 JSON 语法问题（尾逗号、缺引号、截断等）

    返回：
    - 若无法提取，返回 "[]"（避免上层 json.loads 直接报 Expecting value）
    """
    raw = (text or "").strip()

    # 处理空响应，返回空数组避免 JSON 解析错误
    if not raw:
        return "[]"

    # 1) 移除 markdown 代码块标记（宽松处理）
    s = raw
    if s.startswith("```json"):
        s = s[7:]
    elif s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    s = s.strip()

    if not s:
        return "[]"

    # 2) 如果已经是 JSON 开头，用 json_repair 修复后返回
    if s[0] in ("[", "{"):
        return repair_json(s, return_objects=False)

    # 3) 尝试从混杂文本中提取 JSON 数组
    lbr = s.find("[")
    rbr = s.rfind("]")
    if lbr != -1 and rbr != -1 and rbr > lbr:
        candidate = s[lbr : rbr + 1].strip()
        if candidate:
            return repair_json(candidate, return_objects=False)

    # 4) 尝试提取 JSON 对象
    lcb = s.find("{")
    rcb = s.rfind("}")
    if lcb != -1 and rcb != -1 and rcb > lcb:
        candidate = s[lcb : rcb + 1].strip()
        if candidate:
            return repair_json(candidate, return_objects=False)

    # 5) 最后兜底：让 json_repair 尝试修复整个文本
    try:
        repaired = repair_json(s, return_objects=False)
        if repaired and repaired not in ('""', ''):
            return repaired
    except Exception:
        pass

    return "[]"
