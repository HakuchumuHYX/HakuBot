from typing import Optional, Tuple

from plugins.utils.llm import LLMClientConfig, chat_completion

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
    assistant_prefill: Optional[str] = None,
) -> Tuple[str, dict]:
    """
    调用聊天接口，返回 (content, meta)。
    """
    if assistant_prefill:
        messages = messages + [{"role": "assistant", "content": assistant_prefill}]

    result = await chat_completion(
        _chat_llm_config(model=model, max_tokens=max_tokens),
        messages,
        temperature=temperature,
        top_p=top_p,
    )

    content = result.content
    if assistant_prefill:
        content = assistant_prefill + content

    meta = {
        "provider": result.provider,
        "model": result.model,
        "total_tokens": result.usage.total_tokens,
        "elapsed": result.elapsed,
        "raw": result.raw,
    }
    return content, meta
