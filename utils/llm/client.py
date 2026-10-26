import time
import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
from nonebot import logger
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from utils.logging import get_exc_desc


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMClientConfig:
    api_key: str
    base_url: str
    model: str
    provider: str = "openai_compatible"
    timeout: float = 60.0
    proxy: str | None = None
    max_retries: int = 2
    max_tokens: int | None = 65536
    thinking_enabled: bool = False
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    content: str
    model: str
    usage: TokenUsage
    elapsed: float
    # 有工具调用时 content 可能为空；每项是 SDK 对象 model_dump() 的结果
    tool_calls: list[dict] = field(default_factory=list)
    # "length" 表示正文被 max_tokens 截断
    finish_reason: str | None = None


@dataclass
class ImageResult:
    image_url: str


_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def is_retryable_llm_error(exc: Exception) -> bool:
    if isinstance(
        exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)
    ):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS_CODES
    if isinstance(
        exc, (httpx.TimeoutException, httpx.NetworkError, ConnectionError, TimeoutError)
    ):
        return True

    err_msg = str(exc)
    for code in _RETRYABLE_STATUS_CODES:
        if f"API Error {code}" in err_msg or f"status_code={code}" in err_msg:
            return True
    return False


def _make_client(config: LLMClientConfig) -> AsyncOpenAI:
    http_client = httpx.AsyncClient(
        proxy=config.proxy,
        timeout=config.timeout,
    )
    return AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        max_retries=0,
        http_client=http_client,
    )


async def _request(config, operation, **kwargs):
    for attempt in range(max(0, config.max_retries) + 1):
        try:
            return await operation(**kwargs)
        except Exception as exc:
            if not is_retryable_llm_error(exc) or attempt >= config.max_retries:
                raise
            delay = 2**attempt
            logger.info(
                f"LLM 请求失败 (model={kwargs.get('model')})，{delay}s 后重试 "
                f"({attempt + 1}/{config.max_retries}): {get_exc_desc(exc)}"
            )
            await asyncio.sleep(delay)


def _usage_from_response(resp: Any) -> TokenUsage:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _message_content_from_response(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    return str(content or "")


def _tool_calls_from_response(resp: Any) -> list[dict]:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return []
    return [call.model_dump() for call in choices[0].message.tool_calls or []]


def _build_chat_completion_kwargs(
    config: LLMClientConfig,
    messages: list,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    reasoning_effort: str | None = None,
    thinking_enabled: bool | None = None,
    extra_body: dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
) -> dict[str, Any]:
    used_model = model or config.model
    used_max_tokens = max_tokens if max_tokens is not None else config.max_tokens
    use_thinking = (
        config.thinking_enabled if thinking_enabled is None else thinking_enabled
    )

    request_extra_body: dict[str, Any] = dict(config.extra_body or {})
    if extra_body:
        request_extra_body.update(extra_body)

    if use_thinking:
        request_extra_body["thinking"] = {"type": "enabled"}

    used_reasoning_effort = (
        reasoning_effort if reasoning_effort is not None else config.reasoning_effort
    )

    kwargs: dict[str, Any] = {
        "model": used_model,
        "messages": messages,
    }
    if used_max_tokens and used_max_tokens > 0:
        kwargs["max_tokens"] = int(used_max_tokens)
    if used_reasoning_effort:
        kwargs["reasoning_effort"] = used_reasoning_effort
    if request_extra_body:
        kwargs["extra_body"] = request_extra_body
    if response_format:
        kwargs["response_format"] = response_format
    if tools:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice

    if not use_thinking:
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p

    return kwargs


async def chat_completion(
    config: LLMClientConfig,
    messages: list,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    reasoning_effort: str | None = None,
    thinking_enabled: bool | None = None,
    extra_body: dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
) -> ChatResult:
    kwargs = _build_chat_completion_kwargs(
        config,
        messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        reasoning_effort=reasoning_effort,
        thinking_enabled=thinking_enabled,
        extra_body=extra_body,
        response_format=response_format,
        tools=tools,
        tool_choice=tool_choice,
    )

    client = _make_client(config)
    t1 = time.time()
    try:
        resp = await _request(config, client.chat.completions.create, **kwargs)
    finally:
        await client.close()
    elapsed = max(0.0, time.time() - t1)

    return ChatResult(
        content=_message_content_from_response(resp),
        model=str(kwargs["model"]),
        usage=_usage_from_response(resp),
        elapsed=elapsed,
        tool_calls=_tool_calls_from_response(resp),
        finish_reason=resp.choices[0].finish_reason if resp.choices else None,
    )


async def image_generation(
    config: LLMClientConfig,
    *,
    prompt: str,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> ImageResult:
    used_model = model or config.model
    kwargs: dict[str, Any] = {
        "model": used_model,
        "prompt": prompt,
        "n": 1,
    }
    if size:
        kwargs["size"] = size
    if quality:
        kwargs["quality"] = quality

    client = _make_client(config)
    try:
        resp = await _request(config, client.images.generate, **kwargs)
        data = getattr(resp, "data", None) or []
        if not data:
            raise Exception(f"生图 API 返回成功但无数据。完整数据: {resp}")
        item = data[0]
    finally:
        await client.close()

    b64_json = getattr(item, "b64_json", None)
    if isinstance(item, dict):
        b64_json = item.get("b64_json")
    if b64_json:
        return ImageResult(image_url=f"base64://{b64_json}")
    url = getattr(item, "url", None)
    if isinstance(item, dict):
        url = item.get("url")
    if url:
        return ImageResult(image_url=str(url).strip())

    raise Exception(f"生图 API 返回了未知的数据格式: {item}")


async def image_edit(
    config: LLMClientConfig,
    *,
    prompt: str,
    images: list[tuple[str, bytes, str]],
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> ImageResult:
    used_model = model or config.model
    files = [(filename, content, mime_type) for filename, content, mime_type in images]
    kwargs: dict[str, Any] = {
        "model": used_model,
        "prompt": prompt,
        "image": files[0] if len(files) == 1 else files,
        "n": 1,
    }
    if size:
        kwargs["size"] = size
    if quality:
        kwargs["quality"] = quality

    client = _make_client(config)
    try:
        resp = await _request(config, client.images.edit, **kwargs)
        data = getattr(resp, "data", None) or []
        if not data:
            raise Exception(f"图生图 API 返回成功但无数据。完整数据: {resp}")
        item = data[0]
    finally:
        await client.close()

    b64_json = getattr(item, "b64_json", None)
    if isinstance(item, dict):
        b64_json = item.get("b64_json")
    if b64_json:
        return ImageResult(image_url=f"base64://{b64_json}")
    url = getattr(item, "url", None)
    if isinstance(item, dict):
        url = item.get("url")
    if url:
        return ImageResult(image_url=str(url).strip())

    raise Exception(f"图生图 API 返回了未知的数据格式: {item}")
