import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)


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
    proxy: Optional[str] = None
    max_tokens: Optional[int] = 8192
    thinking_enabled: bool = False
    reasoning_effort: Optional[str] = None
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    content: str
    model: str
    provider: str
    usage: TokenUsage
    elapsed: float
    raw: Any = None


@dataclass
class ImageResult:
    image_url: str
    model: str
    provider: str
    elapsed: float
    raw: Any = None


_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def is_retryable_llm_error(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS_CODES
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, ConnectionError, TimeoutError)):
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
        http_client=http_client,
    )


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


async def chat_completion(
    config: LLMClientConfig,
    messages: list,
    *,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    thinking_enabled: Optional[bool] = None,
    extra_body: Optional[dict[str, Any]] = None,
) -> ChatResult:
    used_model = model or config.model
    used_max_tokens = max_tokens if max_tokens is not None else config.max_tokens
    use_thinking = config.thinking_enabled if thinking_enabled is None else thinking_enabled

    request_extra_body: dict[str, Any] = dict(config.extra_body or {})
    if extra_body:
        request_extra_body.update(extra_body)

    if use_thinking:
        request_extra_body["thinking"] = {"type": "enabled"}

    used_reasoning_effort = reasoning_effort if reasoning_effort is not None else config.reasoning_effort

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

    if not use_thinking:
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p

    client = _make_client(config)
    t1 = time.time()
    try:
        resp = await client.chat.completions.create(**kwargs)
    finally:
        await client.close()
    elapsed = max(0.0, time.time() - t1)

    return ChatResult(
        content=_message_content_from_response(resp),
        model=used_model,
        provider=config.provider,
        usage=_usage_from_response(resp),
        elapsed=elapsed,
        raw=resp,
    )


async def image_generation(
    config: LLMClientConfig,
    *,
    prompt: str,
    model: Optional[str] = None,
    size: Optional[str] = None,
    quality: Optional[str] = None,
    size_param: Optional[str] = None,
) -> ImageResult:
    used_model = model or config.model
    kwargs: dict[str, Any] = {
        "model": used_model,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }
    if size:
        kwargs["size"] = size
    if quality:
        kwargs["quality"] = quality
    if size_param:
        kwargs["extra_body"] = {"imageSize": size_param}

    client = _make_client(config)
    t1 = time.time()
    try:
        resp = await client.images.generate(**kwargs)
    finally:
        await client.close()
    elapsed = max(0.0, time.time() - t1)

    data = getattr(resp, "data", None) or []
    if not data:
        raise Exception(f"生图 API 返回成功但无数据。完整数据: {resp}")

    item = data[0]
    b64_json = getattr(item, "b64_json", None)
    if b64_json:
        return ImageResult(
            image_url=f"base64://{b64_json}",
            model=used_model,
            provider=config.provider,
            elapsed=elapsed,
            raw=resp,
        )
    url = getattr(item, "url", None)
    if url:
        return ImageResult(
            image_url=str(url).strip(),
            model=used_model,
            provider=config.provider,
            elapsed=elapsed,
            raw=resp,
        )

    raise Exception(f"生图 API 返回了未知的数据格式: {item}")


async def image_edit(
    config: LLMClientConfig,
    *,
    prompt: str,
    images: list[tuple[str, bytes, str]],
    model: Optional[str] = None,
    size: Optional[str] = None,
    quality: Optional[str] = None,
    size_param: Optional[str] = None,
) -> ImageResult:
    used_model = model or config.model
    files = [
        (filename, content, mime_type)
        for filename, content, mime_type in images
    ]
    kwargs: dict[str, Any] = {
        "model": used_model,
        "prompt": prompt,
        "image": files[0] if len(files) == 1 else files,
        "n": 1,
        "response_format": "b64_json",
    }
    if size:
        kwargs["size"] = size
    if quality:
        kwargs["quality"] = quality
    if size_param:
        kwargs["extra_body"] = {"imageSize": size_param}

    client = _make_client(config)
    t1 = time.time()
    try:
        resp = await client.images.edit(**kwargs)
    finally:
        await client.close()
    elapsed = max(0.0, time.time() - t1)

    data = getattr(resp, "data", None) or []
    if not data:
        raise Exception(f"图生图 API 返回成功但无数据。完整数据: {resp}")

    item = data[0]
    b64_json = getattr(item, "b64_json", None)
    if b64_json:
        return ImageResult(
            image_url=f"base64://{b64_json}",
            model=used_model,
            provider=config.provider,
            elapsed=elapsed,
            raw=resp,
        )
    url = getattr(item, "url", None)
    if url:
        return ImageResult(
            image_url=str(url).strip(),
            model=used_model,
            provider=config.provider,
            elapsed=elapsed,
            raw=resp,
        )

    raise Exception(f"图生图 API 返回了未知的数据格式: {item}")
