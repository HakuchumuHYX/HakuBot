import httpx
import json
import re
from typing import Tuple, Optional, List
from nonebot.log import logger
from .config import plugin_config

HEADERS = {
    "Authorization": f"Bearer {plugin_config.api_key}",
    "Content-Type": "application/json"
}


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", text or "")


async def tavily_search(query: str) -> List[dict]:
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
        "max_results": int(getattr(plugin_config, "web_search_max_results", 5) or 5),
        "search_depth": getattr(plugin_config, "web_search_depth", "basic") or "basic",
        "include_answer": False,
        "include_raw_content": False,
    }

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


async def call_chat_completion(messages: list) -> Tuple[str, str, int]:
    """
    调用聊天接口
    """
    payload = {
        "model": plugin_config.chat_model,
        "messages": messages,
        "max_tokens": 1000
    }

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

        return content, plugin_config.chat_model, total_tokens


async def call_image_generation(content_list: List[dict], extra_context: Optional[str] = None) -> str:
    """
    调用生图接口 (支持多模态输入 + 适配 Chat 协议)

    extra_context:
      用于“生图联网”命令的搜索补充信息，追加在 system_instruction 后面（可选）。
    """
    system_instruction = (
        "Please generate an image based on the user's request. "
        "Return ONLY the image URL in Markdown format, like this: ![image](https://...). "
        "Ensure the response is a valid Markdown image link and nothing else. "
        "生成的图片画风用漫画/二次元画风为佳。"
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
            {"role": "user", "content": content_list}
        ]
    }

    logger.debug(f"正在请求生图 (Chat协议): {plugin_config.base_url}")
    debug_payload = payload.copy()
    debug_payload["messages"] = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": "[(Content with Image Base64 data hidden)]"}
    ]
    logger.debug(f"Payload 概览: {json.dumps(debug_payload, ensure_ascii=False)}")

    try:
        async with httpx.AsyncClient(
                base_url=plugin_config.base_url,
                proxy=plugin_config.proxy,
                timeout=plugin_config.timeout
        ) as client:
            resp = await client.post("/chat/completions", json=payload, headers=HEADERS)

            if resp.status_code != 200:
                logger.error(f"API 返回: {resp.text}")
                raise Exception(f"API Error {resp.status_code}")

            data = resp.json()
    except httpx.TimeoutException:
        raise Exception(f"请求超时，已等待 {plugin_config.timeout} 秒。")

    try:
        choice = data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content")

        if "images" in message and isinstance(message["images"], list) and message["images"]:
            image_obj = message["images"][0]
            if "image_url" in image_obj and "url" in image_obj["image_url"]:
                logger.info("成功从 message.images 字段提取到图片URL")
                return image_obj["image_url"]["url"]

        if content:
            # 优化正则: 支持跨行匹配 (re.DOTALL)，允许 ] 和 ( 之间有空格
            match = re.search(r'!\[.*?\]\s*\((.*?)\)', content, re.DOTALL)
            if match:
                return match.group(1).strip()

            urls = re.findall(r'(https?://[^\s)"]+)', content)
            for url in urls:
                if not url.endswith(('.py', '.html', '.css', '.js')):
                    return url

        if content is None:
            finish_reason = choice.get("finish_reason")
            if finish_reason in ["content_filter", "prohibited_content", "safety"]:
                raise Exception("生图请求被AI模型的安全策略拒绝了 (Safety/Content Policy)。请尝试修改描述。")

            logger.error(f"API返回数据异常。完整数据: {json.dumps(data, ensure_ascii=False)}")
            raise Exception(f"API 返回成功但无内容 (finish_reason: {finish_reason})。")

        preview = content[:50].replace('\n', ' ')
        logger.warning(f"生图失败，完整模型回复: {content}")
        raise Exception(f"模型回复了文本但未包含图片链接: “{preview}...”")

    except (KeyError, IndexError) as e:
        logger.error(f"解析失败: {data}")
        raise Exception(f"API响应格式异常: {e}")
