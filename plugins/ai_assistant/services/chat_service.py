import time
import httpx
from typing import Tuple, Optional, List
from nonebot.log import logger
from ..config import plugin_config
from ..utils import make_headers, get_llm_provider, get_google_api_key, openai_messages_to_gemini


async def _call_chat_completion_google(
    messages: list,
    *,
    max_tokens: Optional[int] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Tuple[str, dict]:
    """
    Google AI Studio / Gemini Developer API: generateContent
    Return shape aligned with image service style: (content, meta).
    """
    rc = plugin_config.resolve("chat")

    system_text, contents = openai_messages_to_gemini(messages)
    used_model = model or plugin_config.chat.model

    used_max_tokens = max_tokens if max_tokens is not None else plugin_config.chat.max_tokens

    payload: dict = {"contents": contents}

    generation_config: dict = {}
    if used_max_tokens and used_max_tokens > 0:
        generation_config["maxOutputTokens"] = int(used_max_tokens)
    if temperature is not None:
        generation_config["temperature"] = float(temperature)
    if top_p is not None:
        generation_config["topP"] = float(top_p)

    if generation_config:
        payload["generationConfig"] = generation_config

    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    api_key = get_google_api_key(rc)
    if not api_key:
        raise Exception("未配置 google_api_key（或 api_key 为空），无法调用 Google AI Studio。")

    base_url = (rc.google_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    t1 = time.time()
    async with httpx.AsyncClient(
        base_url=base_url,
        proxy=plugin_config.proxy,
        timeout=plugin_config.timeout,
    ) as client:
        resp = await client.post(
            f"/models/{used_model}:generateContent",
            params={"key": api_key},
            json=payload,
        )

        if resp.status_code != 200:
            raise Exception(f"Google API Error {resp.status_code}: {resp.text}")

        data = resp.json()
    t2 = time.time()

    # Parse text parts
    text_parts: List[str] = []
    candidates = data.get("candidates") or []
    if candidates:
        c0 = candidates[0] or {}
        content = (c0.get("content") or {})
        parts = content.get("parts") or []
        for p in parts:
            if isinstance(p, dict) and p.get("text"):
                text_parts.append(str(p.get("text")))

    out_text = "\n".join([t for t in text_parts if t is not None]).strip()

    usage = data.get("usageMetadata") or {}
    total_tokens = int(
        usage.get("totalTokenCount")
        or usage.get("total_token_count")
        or 0
    )

    meta = {
        "provider": "google_ai_studio",
        "model": used_model,
        "total_tokens": total_tokens,
        "elapsed": max(0.0, t2 - t1),
    }

    return out_text, meta


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
    调用聊天接口

    返回：
      (content, meta)

    meta 字段：
      - model: 实际模型名
      - total_tokens: token 消耗
      - elapsed: 请求耗时（秒）
      - provider: 提供商标识（google_ai_studio / openai_compat）
    """
    # 注入 assistant prefill（如果有）
    if assistant_prefill:
        messages = messages + [{"role": "assistant", "content": assistant_prefill}]

    rc = plugin_config.resolve("chat")
    provider = get_llm_provider(rc)
    if provider == "google_ai_studio":
        return await _call_chat_completion_google(
            messages,
            max_tokens=max_tokens,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )

    used_model = model or plugin_config.chat.model
    used_max_tokens = max_tokens if max_tokens is not None else plugin_config.chat.max_tokens

    payload = {
        "model": used_model,
        "messages": messages,
    }

    if used_max_tokens and used_max_tokens > 0:
        payload["max_tokens"] = used_max_tokens

    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p

    headers = make_headers(rc.api_key)
    t1 = time.time()
    async with httpx.AsyncClient(
            base_url=rc.base_url,
            proxy=plugin_config.proxy,
            timeout=plugin_config.timeout
    ) as client:
        resp = await client.post("/chat/completions", json=payload, headers=headers)

        if resp.status_code != 200:
            raise Exception(f"API Error {resp.status_code}: {resp.text}")

        data = resp.json()
    t2 = time.time()

    content = data["choices"][0]["message"]["content"]
    total_tokens = data.get("usage", {}).get("total_tokens", 0)

    # 如果使用了 prefill，将 prefill 文本拼接到返回内容前面
    if assistant_prefill:
        content = assistant_prefill + content

    meta = {
        "provider": "openai_compat",
        "model": used_model,
        "total_tokens": total_tokens,
        "elapsed": max(0.0, t2 - t1),
    }

    return content, meta
