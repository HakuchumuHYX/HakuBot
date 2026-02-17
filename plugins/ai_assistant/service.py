import httpx
import json
import re
import html
import math
from typing import Tuple, Optional, List
from nonebot.log import logger
from .config import plugin_config

HEADERS = {
    "Authorization": f"Bearer {plugin_config.api_key}",
    "Content-Type": "application/json",
}


def _get_llm_provider() -> str:
    return (getattr(plugin_config, "provider", None) or "openai_compatible").strip().lower()


def _get_google_api_key() -> str:
    # Gemini Developer API uses API Key (usually via query param ?key=)
    key = (getattr(plugin_config, "google_api_key", None) or "").strip()
    if key:
        return key
    return (getattr(plugin_config, "api_key", None) or "").strip()


def _parse_data_url(data_url: str) -> Tuple[str, str]:
    """
    Parse `data:<mime>;base64,<data>` to (mime, base64_data)
    """
    if not data_url:
        raise ValueError("Empty data url")
    if not data_url.startswith("data:"):
        raise ValueError("Not a data url")
    # data:image/jpeg;base64,AAAA...
    header, b64 = data_url.split(",", 1)
    header = header[5:]  # remove 'data:'
    mime = header.split(";", 1)[0].strip() if ";" in header else header.strip()
    if not mime:
        mime = "application/octet-stream"
    return mime, b64.strip()


def _openai_content_to_gemini_parts(content) -> List[dict]:
    """
    Convert OpenAI-style content (str or [{type,text}/...]) into Gemini parts.
    """
    parts: List[dict] = []
    if content is None:
        return parts
    if isinstance(content, str):
        t = content.strip()
        if t:
            parts.append({"text": t})
        return parts

    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text":
                text = (item.get("text") or "").strip()
                if text:
                    parts.append({"text": text})
            elif t == "image_url":
                url = ((item.get("image_url") or {}).get("url") or "").strip()
                if not url:
                    continue
                # We currently pass `data:*;base64,...` for images
                mime, b64 = _parse_data_url(url)
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": mime,
                            "data": b64,
                        }
                    }
                )
    return parts


def _openai_messages_to_gemini(messages: list) -> Tuple[str, List[dict]]:
    """
    Convert OpenAI messages into (system_text, gemini_contents)
    """
    system_chunks: List[str] = []
    contents: List[dict] = []

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "").strip().lower()
        content = msg.get("content")

        if role == "system":
            if isinstance(content, str) and content.strip():
                system_chunks.append(content.strip())
            elif isinstance(content, list):
                # if someone puts multimodal content in system, just take texts
                for p in _openai_content_to_gemini_parts(content):
                    if "text" in p:
                        system_chunks.append(p["text"])
            continue

        gemini_role = "user" if role == "user" else "model"  # assistant -> model
        parts = _openai_content_to_gemini_parts(content)
        if not parts:
            continue

        contents.append({"role": gemini_role, "parts": parts})

    return "\n".join(system_chunks).strip(), contents


async def _call_chat_completion_google(
    messages: list,
    *,
    max_tokens: int = 1000,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Tuple[str, str, int]:
    """
    Google AI Studio / Gemini Developer API: generateContent
    Keep return signature compatible with call_chat_completion.
    """
    system_text, contents = _openai_messages_to_gemini(messages)
    used_model = model or plugin_config.chat_model

    payload: dict = {"contents": contents}

    generation_config: dict = {"maxOutputTokens": int(max_tokens)}
    if temperature is not None:
        generation_config["temperature"] = float(temperature)
    if top_p is not None:
        generation_config["topP"] = float(top_p)
    payload["generationConfig"] = generation_config

    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    api_key = _get_google_api_key()
    if not api_key:
        raise Exception("未配置 google_api_key（或 api_key 为空），无法调用 Google AI Studio。")

    base_url = (getattr(plugin_config, "google_base_url", None) or "https://generativelanguage.googleapis.com/v1beta").rstrip(
        "/"
    )

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

    return out_text, used_model, total_tokens


async def _call_image_generation_google(
    content_list: List[dict],
    *,
    extra_context: Optional[str] = None,
) -> Tuple[str, dict]:
    """
    Google AI Studio image generation (nano banana / Gemini image models)
    Returns OneBot-compatible base64:// payload.
    """
    used_model = plugin_config.image_model
    api_key = _get_google_api_key()
    if not api_key:
        raise Exception("未配置 google_api_key（或 api_key 为空），无法调用 Google AI Studio 生图。")

    base_url = (getattr(plugin_config, "google_base_url", None) or "https://generativelanguage.googleapis.com/v1beta").rstrip(
        "/"
    )

    system_instruction = (
        "You are an AI specialized in generating 2D anime/manga style art.\n"
        "The style MUST be 2D anime/manga. Do NOT generate realistic or photorealistic images.\n"
        "If you also output text, keep it minimal.\n"
    )
    if extra_context:
        system_instruction += (
            "\n\n[Web Search Context - Reference Only]\n"
            "以下内容仅用于补充事实/外观设定。请提炼其中对画面有用的 3~8 条要点融入绘制，不要照抄整段。"
            "若与用户描述冲突，以用户描述为准。\n\n"
            + extra_context
        )

    parts = _openai_content_to_gemini_parts(content_list)
    payload: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {
            # Request image output. Some models may also output TEXT; we handle both.
            "responseModalities": ["IMAGE", "TEXT"],
        },
    }

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
            raise Exception(f"Google Image API Error {resp.status_code}: {resp.text}")

        data = resp.json()

    # Find inlineData for image
    candidates = data.get("candidates") or []
    if not candidates:
        raise Exception(f"Google Image API 返回无 candidates: {json.dumps(data, ensure_ascii=False)[:500]}")

    c0 = candidates[0] or {}
    content = (c0.get("content") or {})
    parts = content.get("parts") or []

    for p in parts:
        if not isinstance(p, dict):
            continue
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and isinstance(inline, dict):
            b64 = (inline.get("data") or "").strip()
            mime = (inline.get("mimeType") or inline.get("mime_type") or "image/png").strip()
            if b64:
                return f"base64://{b64}", {"mime_type": mime, "provider": "google_ai_studio", "model": used_model}

    # If no image found, provide text for diagnostics
    text_preview = ""
    for p in parts:
        if isinstance(p, dict) and p.get("text"):
            text_preview += str(p.get("text"))
    text_preview = text_preview.strip()
    if text_preview:
        raise Exception(f"Google 生图未返回图片 inlineData，仅返回文本：{text_preview[:200]}")
    raise Exception("Google 生图未返回图片 inlineData。")


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", text or "")


async def tavily_search(
    query: str,
    *,
    max_results: Optional[int] = None,
    search_depth: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> List[dict]:
    """
    使用 Tavily 进行联网搜索（手动命令触发）。
    返回结果格式：[{title, url, content}]
    """
    api_key = getattr(plugin_config, "tavily_api_key", None)
    if not api_key:
        raise Exception("未配置 tavily_api_key，请在 plugins/ai_assistant/config.json 中填写。")

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": int(
            max_results
            if max_results is not None
            else (getattr(plugin_config, "web_search_max_results", 5) or 5)
        ),
        "search_depth": (
            search_depth
            if search_depth is not None
            else (getattr(plugin_config, "web_search_depth", "basic") or "basic")
        ),
        "include_answer": False,
        "include_raw_content": False,
    }

    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    async with httpx.AsyncClient(
        proxy=plugin_config.proxy,
        timeout=plugin_config.timeout,
    ) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
        if resp.status_code != 200:
            raise Exception(f"Tavily API Error {resp.status_code}: {resp.text}")
        data = resp.json()

    results = data.get("results", []) or []
    normalized: List[dict] = []
    for item in results:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or item.get("snippet") or "").strip()
        if not url and not content and not title:
            continue
        normalized.append(
            {
                "title": _strip_control_chars(title),
                "url": _strip_control_chars(url),
                "content": _strip_control_chars(content),
            }
        )

    return normalized


def format_search_results(results: List[dict], max_chars: int = 2500) -> str:
    """
    将 Tavily 搜索结果格式化为可注入 messages 的文本，包含可引用的链接。
    """
    if not results:
        return "（联网搜索未返回结果）"

    lines: List[str] = []
    for idx, r in enumerate(results, start=1):
        title = r.get("title") or ""
        url = r.get("url") or ""
        content = r.get("content") or ""
        snippet = content.replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "..."
        lines.append(f"[{idx}] {title}\n{url}\n摘要：{snippet}")

    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n（搜索结果过长，已截断）"
    return text


def _cleanup_search_text(text: str) -> str:
    """清理用户文本，减少噪声，便于生成搜索 query。"""
    if not text:
        return ""

    t = text.strip()

    # 去掉 Markdown 代码块/行内代码，避免把整段代码丢进搜索
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)
    t = re.sub(r"`[^`]*`", " ", t)

    # 去掉引用前缀
    t = re.sub(r"^>\s*", "", t, flags=re.MULTILINE)

    # 去掉多余空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_core_question(text: str) -> str:
    """
    从较长文本中提取“最像问题的一段”作为 core query。
    策略：优先取最后一个带问号的片段，否则取最后一句。
    """
    if not text:
        return ""

    # 按常见中文/英文标点切分
    parts = re.split(r"[。！？!?；;]\s*", text)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return text.strip()

    # 优先找包含问号的原始片段
    m = re.findall(r"[^。！？!?]*[!?？][^。！？!?]*", text)
    m = [x.strip() for x in m if x.strip()]
    if m:
        return m[-1]

    # 否则取最后一句
    return parts[-1]


def _truncate_query(q: str, max_len: int) -> str:
    q = (q or "").strip()
    if not q:
        return ""
    if max_len and len(q) > max_len:
        return q[:max_len].strip()
    return q


def build_search_queries(raw_text: str, *, mode: str = "chat") -> List[str]:
    """
    启发式生成多条搜索 query（不调用模型）。
    mode:
      - chat: 偏向事实/技术问题检索
      - image: 偏向外观设定/参考资料检索
    """
    max_len = int(getattr(plugin_config, "web_search_query_max_len", 120) or 120)
    max_q = int(getattr(plugin_config, "web_search_num_queries", 3) or 3)

    cleaned = _cleanup_search_text(raw_text)
    core = _extract_core_question(cleaned)

    # 抽取英文/版本号/报错片段等，作为补充关键词
    english_terms = re.findall(r"[A-Za-z][A-Za-z0-9_\-./]{2,}", cleaned)
    versions = re.findall(r"\b\d+(?:\.\d+){1,3}\b", cleaned)

    # 可能的报错行（包含 error/exception/failed 等）
    err_lines = []
    for seg in re.split(r"[。！？!?]\s*|\n+", raw_text or ""):
        s = seg.strip()
        if not s:
            continue
        if re.search(r"(error|exception|failed|traceback|not found|无法|报错|错误)", s, re.I):
            err_lines.append(s)
    err_snippet = err_lines[-1] if err_lines else ""

    # 去重并限制数量
    extras = []
    for x in english_terms + versions:
        x = x.strip()
        if x and x not in extras:
            extras.append(x)
    extras = extras[:6]

    queries: List[str] = []

    q1 = _truncate_query(core, max_len)
    if q1:
        queries.append(q1)

    if extras:
        q2 = _truncate_query(f"{core} {' '.join(extras[:3])}".strip(), max_len)
        if q2 and q2 not in queries:
            queries.append(q2)

    if err_snippet:
        q3 = _truncate_query(err_snippet, max_len)
        if q3 and q3 not in queries:
            queries.append(q3)

    if mode == "image":
        # 生图联网：更偏向“设定/立绘/参考图”
        if queries:
            q_img = _truncate_query(f"{queries[0]} 设定 立绘", max_len)
            if q_img and q_img not in queries:
                queries.append(q_img)

    return queries[:max_q]


async def rewrite_search_queries_with_llm(raw_text: str, *, mode: str = "chat") -> List[str]:
    """
    使用 LLM 对用户输入进行“检索 query 重写”，输出多条短 query。
    失败时返回空数组，由上层回退到启发式。
    """
    max_len = int(getattr(plugin_config, "web_search_query_max_len", 120) or 120)
    max_q = int(getattr(plugin_config, "web_search_num_queries", 3) or 3)

    system = (
        "你是一个搜索查询（web search query）重写器。"
        "你将用户的原始输入改写成适合搜索引擎的短 query。"
        "请输出严格的 JSON，不要输出多余文本。\n\n"
        "输出格式：\n"
        '{ "queries": ["query1", "query2", "query3"] }\n\n'
        "要求：\n"
        f"- queries 数量 1~{max_q}\n"
        f"- 每条 query 不超过 {max_len} 字符\n"
        "- 使用关键词/实体/版本号/错误码；去掉口语、铺垫、无关背景\n"
        "- 不要包含省略号“...”\n"
        "- 不要使用换行\n"
        "- 如果是技术问题，保留库名/平台/错误关键字\n"
        "- 如果是 image 模式，偏向角色/作品名/外观设定/立绘/参考\n"
    )

    user = f"mode={mode}\nraw={_cleanup_search_text(raw_text)}"

    try:
        content, _, _ = await call_chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=256,
        )
    except Exception as e:
        logger.warning(f"LLM query rewrite 调用失败，回退启发式。err={e}")
        return []

    text = (content or "").strip()
    # 去掉可能包裹的代码块
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()

    try:
        obj = json.loads(text)
        queries = obj.get("queries", [])
        if not isinstance(queries, list):
            return []
        out: List[str] = []
        for q in queries:
            if not isinstance(q, str):
                continue
            q = _truncate_query(q.replace("...", "").strip(), max_len)
            if q and q not in out:
                out.append(q)
        return out[:max_q]
    except Exception:
        logger.warning("LLM query rewrite 返回非 JSON，回退启发式。")
        return []


async def web_search_with_rewrite(raw_text: str, *, mode: str = "chat") -> Tuple[List[str], List[dict]]:
    """
    统一入口：对 raw_text 做 query 提炼/重写 → 多 query 搜索 → 合并去重。
    返回：(queries, merged_results)
    """
    # 1) 先启发式
    queries = build_search_queries(raw_text, mode=mode)

    # 2) 需要时用 LLM 重写（B 方案）
    rewrite_enabled = bool(getattr(plugin_config, "web_search_query_rewrite", True))
    use_llm = bool(getattr(plugin_config, "web_search_query_rewrite_use_llm", True))
    trigger_len = int(getattr(plugin_config, "web_search_query_rewrite_llm_trigger_len", 200) or 200)

    if rewrite_enabled and use_llm and raw_text and len(raw_text) >= trigger_len:
        llm_q = await rewrite_search_queries_with_llm(raw_text, mode=mode)
        # 组合：优先使用 LLM 的 query；不足再用启发式补齐
        merged: List[str] = []
        for q in llm_q + queries:
            if q and q not in merged:
                merged.append(q)
        queries = merged[: int(getattr(plugin_config, "web_search_num_queries", 3) or 3)]

    # 3) 如果仍然没有 query，兜底用清理后的原文截断
    if not queries:
        max_len = int(getattr(plugin_config, "web_search_query_max_len", 120) or 120)
        cleaned = _cleanup_search_text(raw_text)
        if cleaned:
            queries = [_truncate_query(cleaned, max_len)]

    # 4) 多 query 搜索 + 合并去重
    total_max = int(getattr(plugin_config, "web_search_max_results", 5) or 5)
    if total_max < 1:
        total_max = 5

    per_query = max(1, int(math.ceil(total_max / max(1, len(queries)))))

    merged_results: List[dict] = []
    seen_urls = set()

    for q in queries:
        try:
            rs = await tavily_search(q, max_results=per_query)
        except Exception as e:
            logger.warning(f"Tavily 搜索失败: query={q} err={e}")
            continue

        for item in rs:
            url = (item.get("url") or "").strip()
            key = url or (item.get("title") or "") + (item.get("content") or "")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            merged_results.append(item)

            if len(merged_results) >= total_max:
                break
        if len(merged_results) >= total_max:
            break

    return queries, merged_results


async def call_chat_completion(
    messages: list,
    *,
    max_tokens: int = 1000,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Tuple[str, str, int]:
    """
    调用聊天接口

    max_tokens:
      用于控制不同用途的 token 消耗（例如：检索 query 重写通常只需要很少 token）。
    model:
      可选覆盖模型（默认使用 plugin_config.chat_model）。
    """
    provider = _get_llm_provider()
    if provider == "google_ai_studio":
        return await _call_chat_completion_google(
            messages,
            max_tokens=max_tokens,
            model=model,
            temperature=temperature,
            top_p=top_p,
        )

    payload = {
        "model": model or plugin_config.chat_model,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p

    async with httpx.AsyncClient(
            base_url=plugin_config.base_url,
            proxy=plugin_config.proxy,
            timeout=plugin_config.timeout
    ) as client:
        resp = await client.post("/chat/completions", json=payload, headers=HEADERS)

        if resp.status_code != 200:
            raise Exception(f"API Error {resp.status_code}: {resp.text}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

        return content, (model or plugin_config.chat_model), total_tokens


async def call_image_generation(content_list: List[dict], extra_context: Optional[str] = None) -> Tuple[str, dict]:
    """
    调用生图接口 (支持多模态输入 + 适配 Chat 协议)

    优化点：
    - 诊断增强：尽可能打印/返回 finish_reason/native_finish_reason/usage 等信息
    - 协议兜底：成功输出 Markdown 图片链接；失败输出 JSON error
    - 自动安全降级重试：疑似被拦截/空内容时，将用户描述改写为更合规的 SFW 版本再重试

    返回：
      (image_url, meta)
      meta.used_safe_rewrite: 是否进行过“合规化改写后重试”
      meta.safe_rewrite_attempts: 改写重试次数
    """

    provider = _get_llm_provider()
    if provider == "google_ai_studio":
        return await _call_image_generation_google(content_list, extra_context=extra_context)

    def _normalize_content_list_for_retry(original: List[dict], new_text: str) -> List[dict]:
        """保留图片输入，仅替换/合并文本输入为一段。"""
        out: List[dict] = []
        for item in original:
            if item.get("type") == "image_url":
                out.append(item)
        if new_text.strip():
            out.insert(0, {"type": "text", "text": new_text.strip()})
        return out

    def _extract_text_from_content_list(items: List[dict]) -> str:
        texts = []
        for item in items:
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                t = item.get("text", "").strip()
                if t:
                    texts.append(t)
        return "\n".join(texts).strip()

    async def _rewrite_to_safe_prompt(raw_prompt: str) -> str:
        """将用户绘图描述改写为更合规的 SFW 版本（不是绕过，仅做中性化表达）。"""
        model = getattr(plugin_config, "image_safe_rewrite_model", None) or plugin_config.chat_model
        max_tokens = int(getattr(plugin_config, "image_safe_rewrite_max_tokens", 256) or 256)

        system = (
            "你是一个“绘图提示词安全改写器”。\n"
            "任务：把用户的绘图描述改写成更容易通过内容安全策略的版本（SFW/合规）。\n"
            "要求：\n"
            "1) 保留用户的主体、场景、风格、构图意图\n"
            "2) 删除或弱化可能触发拦截的内容（例如：未成年人相关、露骨性内容、过度暴力血腥、自残、仇恨等）\n"
            "3) 不要添加新的敏感内容\n"
            "4) 输出只包含最终改写后的 prompt，不要解释\n"
        )

        user = raw_prompt
        if extra_context:
            user = (
                user
                + "\n\n[参考设定/资料（仅用于外观设定，不要照抄）]\n"
                + extra_context
            )

        content, _, _ = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            model=model,
        )
        return (content or "").strip()

    async def _request_once(items: List[dict]) -> str:
        system_instruction = (
            "You are an AI specialized in generating 2D anime/manga style art.\n"
            "The style MUST be 2D anime/manga. Do NOT generate realistic or photorealistic images.\n"
            "Output policy:\n"
            "1) If successful: return ONLY a Markdown image link: ![image](https://...)\n"
            "2) If you cannot comply (e.g. safety/policy/unsupported): return ONLY a JSON object like:\n"
            '   {"error":{"type":"safety_refusal","message":"...","suggestion":"..."} }\n'
            "Do not output any other text.\n"
            "生成的图片画风必须是二次元/动漫风格。禁止生成写实/光影写实的图片。"
        )

        if extra_context:
            system_instruction += (
                "\n\n[Web Search Context - Reference Only]\n"
                "以下内容仅用于补充事实/外观设定。请提炼其中对画面有用的 3~8 条要点融入绘制，不要照抄整段。"
                "若与用户描述冲突，以用户描述为准。"
                "\n\n"
                + extra_context
            )

        payload = {
            "model": plugin_config.image_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": items},
            ],
        }

        logger.debug(f"正在请求生图 (Chat协议): {plugin_config.base_url}")
        debug_payload = payload.copy()
        debug_payload["messages"] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": "[(Content with Image Base64 data hidden)]"},
        ]
        logger.debug(f"Payload 概览: {json.dumps(debug_payload, ensure_ascii=False)}")

        try:
            async with httpx.AsyncClient(
                base_url=plugin_config.base_url,
                proxy=plugin_config.proxy,
                timeout=plugin_config.timeout,
            ) as client:
                resp = await client.post("/chat/completions", json=payload, headers=HEADERS)
                resp_text = resp.text

                if resp.status_code != 200:
                    logger.error(f"生图 API 非 200。status={resp.status_code} body={resp_text}")
                    raise Exception(f"生图 API Error {resp.status_code}")

                data = resp.json()
        except httpx.TimeoutException:
            raise Exception(f"生图请求超时，已等待 {plugin_config.timeout} 秒。")

        # 诊断：尽可能把关键字段打出来
        try:
            choice = (data.get("choices") or [None])[0] or {}
            message = choice.get("message") or {}
            content = message.get("content")
            finish_reason = choice.get("finish_reason")
            native_finish_reason = choice.get("native_finish_reason")
            usage = data.get("usage") or {}

            logger.debug(
                "生图返回摘要: "
                + json.dumps(
                    {
                        "id": data.get("id"),
                        "model": data.get("model"),
                        "finish_reason": finish_reason,
                        "native_finish_reason": native_finish_reason,
                        "has_images": bool(message.get("images")),
                        "content_is_null": content is None,
                        "usage": usage,
                    },
                    ensure_ascii=False,
                )
            )

            # 1) 优先从 message.images 拿 URL
            if "images" in message and isinstance(message["images"], list) and message["images"]:
                image_obj = message["images"][0]
                if "image_url" in image_obj and "url" in image_obj["image_url"]:
                    logger.info("成功从 message.images 字段提取到图片URL")
                    return html.unescape(image_obj["image_url"]["url"]).strip()

            # 2) content 存在：尝试解析 markdown / URL / JSON error
            if isinstance(content, str) and content.strip():
                c = content.strip()

                # JSON error（允许被 ```json 包裹）
                c2 = re.sub(r"^```(?:json)?\s*", "", c, flags=re.I).strip()
                c2 = re.sub(r"\s*```$", "", c2).strip()
                if c2.startswith("{") and c2.endswith("}"):
                    try:
                        obj = json.loads(c2)
                        if isinstance(obj, dict) and "error" in obj:
                            err = obj.get("error") or {}
                            err_type = err.get("type") or "unknown"
                            err_msg = err.get("message") or "模型未返回详细原因"
                            err_sug = err.get("suggestion") or ""
                            raise Exception(f"生图被拒绝/失败: {err_type} - {err_msg}" + (f"（建议：{err_sug}）" if err_sug else ""))
                    except json.JSONDecodeError:
                        pass

                match = re.search(r"!\[.*?\]\s*\((.*?)\)", c, re.DOTALL)
                if match:
                    return html.unescape(match.group(1)).strip()

                urls = re.findall(r"(https?://[^\s)\"']+)", c)
                for url in urls:
                    if not url.endswith((".py", ".html", ".css", ".js")):
                        return html.unescape(url).strip()

                preview = c[:120].replace("\n", " ")
                raise Exception(f"生图返回了文本但未包含图片链接: “{preview}...”")

            # 3) content 为 None 或空：强诊断
            # 某些网关/上游在拦截时会返回 200 + content=null + tokens=0
            tokens0 = (
                int(usage.get("total_tokens") or 0) == 0
                and int(usage.get("prompt_tokens") or 0) == 0
                and int(usage.get("completion_tokens") or 0) == 0
            )
            if content is None and tokens0:
                logger.error(f"生图返回空内容且 tokens=0，疑似安全拦截/网关未映射原因。完整数据: {json.dumps(data, ensure_ascii=False)}")
                raise Exception(
                    "生图返回空内容（tokens=0），疑似被安全策略拦截或网关未返回拒绝原因。"
                    f"finish_reason={finish_reason} native_finish_reason={native_finish_reason}"
                )

            if content is None:
                logger.error(f"生图返回 content=null。完整数据: {json.dumps(data, ensure_ascii=False)}")
                raise Exception(f"生图 API 返回成功但无内容 (finish_reason={finish_reason}, native_finish_reason={native_finish_reason})")

            raise Exception("生图失败：返回内容为空或不可解析。")

        except Exception:
            # 让上层处理重试逻辑
            raise

    meta = {
        "used_safe_rewrite": False,
        "safe_rewrite_attempts": 0,
    }

    # ---------- main flow: request + optional safe retry ----------
    retry_enabled = bool(getattr(plugin_config, "image_retry_on_empty", True))
    max_retry = int(getattr(plugin_config, "image_retry_max_times", 1) or 1)

    last_err: Optional[Exception] = None
    current_items = content_list

    for attempt in range(max_retry + 1):
        try:
            image_url = await _request_once(current_items)
            return image_url, meta
        except Exception as e:
            last_err = e
            msg = str(e)

            # 判定是否值得做“安全改写重试”
            retriable = (
                "疑似被安全策略拦截" in msg
                or "content=null" in msg
                or "tokens=0" in msg
                or "safety" in msg.lower()
                or "policy" in msg.lower()
            )

            if not (retry_enabled and attempt < max_retry and retriable):
                raise

            raw_prompt = _extract_text_from_content_list(content_list)
            if not raw_prompt:
                # 没有文本可改写（例如纯图输入），就不重试
                raise

            logger.warning(f"生图失败，尝试安全改写后重试。attempt={attempt+1}/{max_retry} err={msg}")
            safe_prompt = await _rewrite_to_safe_prompt(raw_prompt)
            if not safe_prompt:
                raise

            meta["used_safe_rewrite"] = True
            meta["safe_rewrite_attempts"] = int(meta.get("safe_rewrite_attempts") or 0) + 1

            current_items = _normalize_content_list_for_retry(content_list, safe_prompt)

    # 理论上不会到这里
    raise last_err or Exception("生图失败：未知错误。")
