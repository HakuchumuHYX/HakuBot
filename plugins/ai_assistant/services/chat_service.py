from typing import Optional

from plugins.utils.llm import ChatResult, LLMClientConfig, chat_completion

from ..config import plugin_config


def _chat_llm_config(model: Optional[str] = None, max_tokens: Optional[int] = None) -> LLMClientConfig:
    rc = plugin_config.resolve("chat")
    return LLMClientConfig(
        provider=rc.provider,
        api_key=rc.api_key,
        base_url=rc.base_url,
        model=model or plugin_config.chat.model,
        timeout=plugin_config.timeout,
        proxy=plugin_config.proxy,
        max_tokens=max_tokens if max_tokens is not None else plugin_config.chat.max_tokens,
        thinking_enabled=plugin_config.chat.thinking_enabled,
        reasoning_effort=plugin_config.chat.reasoning_effort,
        extra_body=plugin_config.chat.extra_body,
    )


async def call_chat_completion(
    messages: list,
    *,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> ChatResult:
    """调用配置的 OpenAI-compatible Chat API。"""
    result = await chat_completion(
        _chat_llm_config(model=model, max_tokens=max_tokens),
        messages,
        temperature=temperature,
        top_p=top_p,
    )
    if not result.content.strip():
        raise ValueError("聊天 API 返回成功，但响应中没有文本内容。")
    return result
