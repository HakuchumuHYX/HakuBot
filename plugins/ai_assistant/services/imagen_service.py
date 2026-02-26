import httpx
import json
import re
from typing import Tuple, Optional, List
from nonebot.log import logger
from ..config import plugin_config
from ..utils import make_headers, get_llm_provider, get_google_api_key, parse_data_url, openai_content_to_gemini_parts
from .chat_service import call_chat_completion

async def _call_image_generation_google(
    content_list: List[dict],
    *,
    extra_context: Optional[str] = None,
) -> Tuple[str, dict]:
    """
    Google AI Studio image generation (nano banana / Gemini image models)
    Returns OneBot-compatible base64:// payload.
    """
    rc = plugin_config.resolve("image")
    used_model = plugin_config.image.model
    api_key = get_google_api_key(rc)
    if not api_key:
        raise Exception("未配置 google_api_key（或 api_key 为空），无法调用 Google AI Studio 生图。")

    base_url = (rc.google_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

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

    parts = openai_content_to_gemini_parts(content_list)
    # 将画风约束直接注入 user 消息，提高模型对画风的遵从度
    style_hint = {"text": "（画风要求：必须是二次元/动漫/manga 风格，禁止写实/照片风格）"}
    parts.insert(0, style_hint)
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


async def _call_image_generation_chat_compat(
    content_list: List[dict],
    *,
    extra_context: Optional[str] = None,
) -> Tuple[str, dict]:
    """
    通过 /v1/chat/completions 端点调用生图模型（适配仅提供 chat 协议的中转商）。
    将生图 prompt 包装为 chat messages，解析模型返回中的图片数据。
    """
    used_model = plugin_config.image.model

    # --- 构建 system prompt ---
    system_text = (
        "You are an AI specialized in generating 2D anime/manga style art.\n"
        "The style MUST be 2D anime/manga. Do NOT generate realistic or photorealistic images.\n"
        "You MUST generate an image in your response.\n"
    )
    if extra_context:
        system_text += (
            "\n\n[Web Search Context - Reference Only]\n"
            "以下内容仅用于补充事实/外观设定。请提炼其中对画面有用的 3~8 条要点融入绘制，不要照抄整段。"
            "若与用户描述冲突，以用户描述为准。\n\n"
            + extra_context
        )

    # --- 构建 user message content（多模态：文本 + 图片）---
    user_content: list = []
    style_hint = "（画风要求：必须是二次元/动漫/manga 风格，禁止写实/照片风格）"
    user_content.append({"type": "text", "text": style_hint})

    for item in content_list:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text_val = (item.get("text") or "").strip()
            if text_val:
                user_content.append({"type": "text", "text": text_val})
        elif item.get("type") == "image_url":
            user_content.append(item)

    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_content},
    ]

    payload = {
        "model": used_model,
        "messages": messages,
    }

    rc = plugin_config.resolve("image")
    headers = make_headers(rc.api_key)

    logger.debug(f"[chat_compat] 正在通过 /chat/completions 请求生图: model={used_model}")

    async with httpx.AsyncClient(
        base_url=rc.base_url,
        proxy=plugin_config.proxy,
        timeout=plugin_config.timeout,
    ) as client:
        resp = await client.post("/chat/completions", json=payload, headers=headers)

        if resp.status_code != 200:
            err_text = resp.text
            logger.error(f"[chat_compat] 生图 API 非 200。status={resp.status_code} body={err_text}")
            try:
                err_obj = json.loads(err_text).get("error", {})
                if err_obj:
                    err_msg = err_obj.get("message") or err_obj.get("type") or err_text
                    raise Exception(f"生图被拒绝/失败: {err_msg}")
            except json.JSONDecodeError:
                pass
            raise Exception(f"生图 API Error {resp.status_code}")

        data = resp.json()

    # --- 解析响应，提取图片 ---
    choices = data.get("choices") or []
    if not choices:
        raise Exception(f"[chat_compat] 生图返回无 choices: {json.dumps(data, ensure_ascii=False)[:500]}")

    message = (choices[0].get("message") or {})
    msg_content = message.get("content")

    # 情况0: message.images 中包含图片 (某些中转商的自定义格式)
    msg_images = message.get("images")
    if isinstance(msg_images, list) and msg_images:
        for part in msg_images:
            if not isinstance(part, dict):
                continue
            p_type = part.get("type", "")

            # 0a: type=image_url
            if p_type == "image_url":
                url = ((part.get("image_url") or {}).get("url") or "").strip()
                if url.startswith("data:"):
                    mime, b64 = parse_data_url(url)
                    return f"base64://{b64}", {"mime_type": mime, "provider": "chat_compat", "model": used_model}
                elif url:
                    return url, {"provider": "chat_compat", "model": used_model}

            # 0b: type=image
            if p_type == "image":
                b64 = (part.get("data") or part.get("base64") or "").strip()
                if b64:
                    mime = (part.get("mime_type") or part.get("mimeType") or "image/png").strip()
                    return f"base64://{b64}", {"mime_type": mime, "provider": "chat_compat", "model": used_model}
                url = (part.get("url") or "").strip()
                if url:
                    return url, {"provider": "chat_compat", "model": used_model}

    # 情况1: content 是 list（多模态响应，包含 image_url 类型的 part）
    if isinstance(msg_content, list):
        for part in msg_content:
            if not isinstance(part, dict):
                continue
            p_type = part.get("type", "")

            # 1a: type=image_url，内含 url 字段（可能是真实 URL 或 data URI）
            if p_type == "image_url":
                url = ((part.get("image_url") or {}).get("url") or "").strip()
                if url.startswith("data:"):
                    mime, b64 = parse_data_url(url)
                    return f"base64://{b64}", {"mime_type": mime, "provider": "chat_compat", "model": used_model}
                elif url:
                    return url, {"provider": "chat_compat", "model": used_model}

            # 1b: type=image，内含 base64/url（部分中转商的自定义格式）
            if p_type == "image":
                b64 = (part.get("data") or part.get("base64") or "").strip()
                if b64:
                    mime = (part.get("mime_type") or part.get("mimeType") or "image/png").strip()
                    return f"base64://{b64}", {"mime_type": mime, "provider": "chat_compat", "model": used_model}
                url = (part.get("url") or "").strip()
                if url:
                    return url, {"provider": "chat_compat", "model": used_model}

        # 如果 list 中没找到图片，拼接文本做诊断
        text_parts = [str(p.get("text", "")) for p in msg_content if isinstance(p, dict) and p.get("text")]
        msg_content = "\n".join(text_parts).strip()

    # 情况2: content 是 string
    if isinstance(msg_content, str) and msg_content.strip():
        text = msg_content.strip()

        # 2a: 检查是否包含 data URI (data:image/...;base64,...)
        data_uri_match = re.search(r'data:(image/[^;]+);base64,([A-Za-z0-9+/=\s]+)', text)
        if data_uri_match:
            mime = data_uri_match.group(1).strip()
            b64 = data_uri_match.group(2).strip().replace("\n", "").replace(" ", "")
            return f"base64://{b64}", {"mime_type": mime, "provider": "chat_compat", "model": used_model}

        # 2b: 检查 Markdown 图片链接 ![...](url)
        md_img_match = re.search(r'!\[.*?\]\((https?://\S+)\)', text)
        if md_img_match:
            url = md_img_match.group(1).strip()
            return url, {"provider": "chat_compat", "model": used_model}

        # 2c: 检查裸 URL
        url_match = re.search(r'(https?://\S+\.(?:png|jpg|jpeg|gif|webp|bmp)(?:\?\S*)?)', text, re.IGNORECASE)
        if url_match:
            url = url_match.group(1).strip()
            return url, {"provider": "chat_compat", "model": used_model}

        # 没找到图片
        raise Exception(f"[chat_compat] 生图模型未返回图片，仅返回文本：{text[:300]}")

    raise Exception(f"[chat_compat] 生图模型返回内容为空或无法解析。raw={json.dumps(data, ensure_ascii=False)[:500]}")


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

    rc = plugin_config.resolve("image")
    provider = get_llm_provider(rc)
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
        model = getattr(plugin_config.image, "safe_rewrite_model", None) or plugin_config.chat.model
        max_tokens = int(getattr(plugin_config.image, "safe_rewrite_max_tokens", 256) or 256)

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
        prompt_texts = []
        image_parts = []
        
        # 提取用户输入的文本和图片
        for item in items:
            if isinstance(item, dict):
                t = item.get("type")
                if t == "text":
                    text_val = item.get("text", "").strip()
                    if text_val:
                        prompt_texts.append(text_val)
                elif t == "image_url":
                    url = ((item.get("image_url") or {}).get("url") or "").strip()
                    if url.startswith("data:"):
                        mime, b64 = parse_data_url(url)
                        import base64
                        image_parts.append((mime, base64.b64decode(b64)))

        # 加入风格约束和可能存在的联网搜索附加上下文
        style_hint = "（画风要求：必须是二次元/动漫/manga 风格，禁止写实/照片风格）"
        if style_hint not in prompt_texts:
            prompt_texts.insert(0, style_hint)
            
        final_prompt = "\n".join(prompt_texts)
        if extra_context:
            final_prompt += (
                "\n\n[Web Search Context - Reference Only]\n"
                "以下内容仅用于补充事实/外观设定。请提炼其中对画面有用的 3~8 条要点融入绘制，不要照抄整段。"
                "若与用户描述冲突，以用户描述为准。\n\n"
                + extra_context
            )
            
        model_name = plugin_config.image.model
        
        rc = plugin_config.resolve("image")
        try:
            async with httpx.AsyncClient(
                base_url=rc.base_url,
                proxy=plugin_config.proxy,
                timeout=plugin_config.timeout,
            ) as client:
                if image_parts:
                    # 图生图请求 /v1/images/edits
                    logger.debug(f"正在请求图生图 (images/edits): {plugin_config.base_url}")
                    files = {}
                    for i, (mime, img_bytes) in enumerate(image_parts):
                        ext = mime.split("/")[-1] if "/" in mime else "png"
                        if ext == "jpeg":
                            ext = "jpg"
                        files[f"image{i+1}"] = (f"ref{i+1}.{ext}", img_bytes, mime)
                        
                    # 为兼容 OpenAI 标准，如果仅有一张图，也可同时放入 image 字段
                    if len(image_parts) == 1:
                        mime, img_bytes = image_parts[0]
                        ext = mime.split("/")[-1] if "/" in mime else "png"
                        if ext == "jpeg":
                            ext = "jpg"
                        files["image"] = (f"ref_main.{ext}", img_bytes, mime)
                        
                    data = {
                        "prompt": final_prompt,
                        "model": model_name,
                        "n": 1,
                        "response_format": "b64_json"
                    }
                    if plugin_config.image.size:
                        data["size"] = plugin_config.image.size
                    if plugin_config.image.quality:
                        data["quality"] = plugin_config.image.quality
                    if plugin_config.image.size_param:
                        data["imageSize"] = plugin_config.image.size_param
                        
                    auth_headers = {"Authorization": f"Bearer {rc.api_key}"}
                    
                    resp = await client.post("/images/edits", headers=auth_headers, data=data, files=files)
                else:
                    # 文生图请求 /v1/images/generations
                    logger.debug(f"正在请求文生图 (images/generations): {plugin_config.base_url}")
                    payload = {
                        "prompt": final_prompt,
                        "model": model_name,
                        "n": 1,
                        "response_format": "b64_json"
                    }
                    if plugin_config.image.size:
                        payload["size"] = plugin_config.image.size
                    if plugin_config.image.quality:
                        payload["quality"] = plugin_config.image.quality
                    if plugin_config.image.size_param:
                        payload["imageSize"] = plugin_config.image.size_param
                        
                    gen_headers = make_headers(rc.api_key)
                    resp = await client.post("/images/generations", headers=gen_headers, json=payload)
                    
                resp_text = resp.text

                if resp.status_code != 200:
                    logger.error(f"生图 API 非 200。status={resp.status_code} body={resp_text}")
                    # 尝试解析错误信息
                    try:
                        err_obj = json.loads(resp_text).get("error", {})
                        if err_obj:
                            err_msg = err_obj.get("message") or err_obj.get("type") or resp_text
                            raise Exception(f"生图被拒绝/失败: {err_msg}")
                    except json.JSONDecodeError:
                        pass
                    raise Exception(f"生图 API Error {resp.status_code}")

                data = resp.json()
                
        except httpx.TimeoutException:
            raise Exception(f"生图请求超时，已等待 {plugin_config.timeout} 秒。")

        try:
            # 解析 response_format="b64_json" 或 "url" 的返回结果
            resp_data = data.get("data", [])
            if not resp_data:
                raise Exception(f"生图 API 返回成功但无数据。完整数据: {json.dumps(data, ensure_ascii=False)}")
                
            img_item = resp_data[0]
            if "b64_json" in img_item:
                logger.info("成功提取生图结果 (b64_json)")
                return f"base64://{img_item['b64_json']}"
            elif "url" in img_item:
                logger.info("成功提取生图结果 (url)")
                return img_item["url"].strip()
            else:
                raise Exception(f"生图 API 返回了未知的数据格式: {json.dumps(img_item, ensure_ascii=False)}")

        except Exception as e:
            if not isinstance(e, Exception) or "生图被拒绝/失败" not in str(e):
                logger.error(f"解析生图结果失败: {e}")
            raise

    meta = {
        "used_safe_rewrite": False,
        "safe_rewrite_attempts": 0,
    }

    # ---------- main flow: request + optional safe retry ----------
    retry_enabled = bool(getattr(plugin_config.image, "retry_on_empty", True))
    max_retry = int(getattr(plugin_config.image, "retry_max_times", 1) or 1)

    last_err: Optional[Exception] = None
    current_items = content_list

    for attempt in range(max_retry + 1):
        try:
            if plugin_config.image.use_chat_endpoint:
                image_url, _chat_meta = await _call_image_generation_chat_compat(
                    current_items, extra_context=extra_context
                )
            else:
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
