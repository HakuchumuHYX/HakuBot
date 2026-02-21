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

    Provider:
    - openai_compatible: POST /chat/completions (OpenAI 兼容接口)
    - google_ai_studio: POST /models/{model}:generateContent (Gemini Developer API / AI Studio 官方接口)

    Args:
        messages: OpenAI 风格消息列表
        temperature: 温度参数
        max_retries: 最大重试次数（默认3次）
        base_delay: 基础延迟秒数，实际延迟为 base_delay * (2 ** attempt)

    Returns: (content, token_usage)

    说明：
    - OpenAI 兼容接口从 usage 字段读取 token
    - Google AI Studio 从 usageMetadata 字段读取 token
    - 遇到可重试错误（网络超时、429限流、5xx错误）会自动重试（指数退避）
    """
    provider = (getattr(plugin_config.llm, "provider", None) or "openai_compatible").strip().lower()

    def _google_api_key() -> str:
        return (
            (getattr(plugin_config.llm, "google_api_key", None) or "").strip()
            or (getattr(plugin_config.llm, "api_key", None) or "").strip()
        )

    def _openai_messages_to_gemini(messages_: list) -> tuple[str, list[dict]]:
        system_chunks: list[str] = []
        contents: list[dict] = []

        for m in messages_ or []:
            if not isinstance(m, dict):
                continue
            role = (m.get("role") or "").strip().lower()
            content = m.get("content")

            if role == "system":
                if isinstance(content, str) and content.strip():
                    system_chunks.append(content.strip())
                continue

            # group_daily_analysis 的 messages 只会是纯文本；这里保持简单
            if isinstance(content, str):
                text = content.strip()
            else:
                text = str(content).strip() if content is not None else ""

            if not text:
                continue

            gemini_role = "user" if role == "user" else "model"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})

        return "\n".join(system_chunks).strip(), contents

    # --- build request payload for openai-compatible ---
    headers_openai = {
        "Authorization": f"Bearer {plugin_config.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload_openai = {
        "model": plugin_config.llm.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 8192,  # 增大输出上限，避免长 JSON 截断
    }

    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with _LLM_SEMAPHORE:
                if provider == "google_ai_studio":
                    api_key = _google_api_key()
                    if not api_key:
                        raise Exception("未配置 google_api_key（或 api_key 为空），无法调用 Google AI Studio。")

                    base_url = (
                        getattr(plugin_config.llm, "google_base_url", None)
                        or "https://generativelanguage.googleapis.com/v1beta"
                    ).rstrip("/")

                    system_text, contents = _openai_messages_to_gemini(messages)
                    payload_google: dict = {
                        "contents": contents,
                        "generationConfig": {
                            "temperature": float(temperature),
                            "maxOutputTokens": 8192,
                        },
                    }
                    if system_text:
                        payload_google["systemInstruction"] = {"parts": [{"text": system_text}]}

                    async with httpx.AsyncClient(
                        base_url=base_url,
                        proxy=plugin_config.llm.proxy,
                        timeout=plugin_config.llm.timeout,
                    ) as client:
                        resp = await client.post(
                            f"/models/{plugin_config.llm.model}:generateContent",
                            params={"key": api_key},
                            json=payload_google,
                        )
                else:
                    async with httpx.AsyncClient(
                        base_url=plugin_config.llm.base_url,
                        proxy=plugin_config.llm.proxy,
                        timeout=plugin_config.llm.timeout,
                    ) as client:
                        resp = await client.post("/chat/completions", json=payload_openai, headers=headers_openai)

            if resp.status_code != 200:
                error_msg = f"API Error {resp.status_code}: {resp.text}"
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"LLM API 返回 {resp.status_code}，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"LLM API Error: {error_msg}")
                raise Exception(error_msg)

            data = resp.json()

            if provider == "google_ai_studio":
                # Parse Gemini response
                candidates = data.get("candidates") or []
                text_parts: list[str] = []
                if candidates:
                    c0 = candidates[0] or {}
                    c0_content = (c0.get("content") or {})
                    parts = c0_content.get("parts") or []
                    for p in parts:
                        if isinstance(p, dict) and p.get("text"):
                            text_parts.append(str(p.get("text")))
                content = "\n".join(text_parts).strip()

                usage = data.get("usageMetadata") or {}
                token_usage = TokenUsage(
                    prompt_tokens=int(usage.get("promptTokenCount") or 0),
                    completion_tokens=int(usage.get("candidatesTokenCount") or 0),
                    total_tokens=int(usage.get("totalTokenCount") or 0),
                )
            else:
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                token_usage = TokenUsage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )

            if attempt > 0:
                logger.info(f"LLM 调用在第 {attempt + 1} 次尝试后成功")

            return content, token_usage

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
    尝试从模型输出中“提取并修复”JSON字符串。

    目标：
    - 允许模型输出解释性文字 + JSON（常见于 Gemini），我们尽量把 JSON 抠出来再交给 json.loads
    - 支持 ```json ... ``` / ``` ... ``` 代码块
    - 支持提取最外层 JSON 数组 `[...]` 或对象 `{...}`

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

    # 2) 如果已经是 JSON 开头，直接返回
    if s[0] in ("[", "{"):
        return s

    # 3) 尝试从混杂文本中提取 JSON 数组
    lbr = s.find("[")
    rbr = s.rfind("]")
    if lbr != -1 and rbr != -1 and rbr > lbr:
        candidate = s[lbr : rbr + 1].strip()
        if candidate:
            return candidate

    # 4) 尝试提取 JSON 对象
    lcb = s.find("{")
    rcb = s.rfind("}")
    if lcb != -1 and rcb != -1 and rcb > lcb:
        candidate = s[lcb : rcb + 1].strip()
        if candidate:
            return candidate

    return "[]"
