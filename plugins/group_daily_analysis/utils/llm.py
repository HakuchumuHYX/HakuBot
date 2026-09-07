"""The analysis plugin's own request configuration and concurrency policy."""

import asyncio
from utils.llm.client import LLMClientConfig, chat_completion, is_retryable_llm_error
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import TokenUsage

_semaphore = None


async def call_chat_completion(
    messages, temperature=0.5, max_retries=3, response_format=None, max_tokens=None
):
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(
            max(1, int(plugin_config.max_concurrent_tasks or 1))
        )
    source = plugin_config.llm
    config = LLMClientConfig(
        api_key=source.api_key,
        base_url=source.base_url,
        model=source.model,
        provider=source.provider,
        timeout=source.timeout,
        proxy=source.proxy,
        max_tokens=source.max_tokens,
        thinking_enabled=source.thinking_enabled,
        reasoning_effort=source.reasoning_effort,
        extra_body=source.extra_body,
        max_retries=max(0, max_retries - 1),
    )
    async with _semaphore:
        result = await chat_completion(
            config,
            messages,
            temperature=temperature,
            response_format=response_format,
            max_tokens=max_tokens,
        )
    return result.content, TokenUsage(
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        total_tokens=result.usage.total_tokens,
    )
