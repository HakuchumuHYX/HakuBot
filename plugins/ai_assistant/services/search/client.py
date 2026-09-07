import httpx
import json
import re
import math
from typing import Any, Tuple, Optional, List
from nonebot.log import logger
from pydantic import Field
from plugins.ai_assistant.config import StrictBaseModel, plugin_config
from plugins.ai_assistant.services.chat_service import call_chat_completion
from plugins.ai_assistant.services.search import normalization as _normalization


async def tavily_search_full(
    query: str,
    *,
    max_results: Optional[int] = None,
    search_depth: Optional[str] = None,
    include_answer: Any = False,
    include_raw_content: Any = False,
    include_images: bool = False,
    include_image_descriptions: bool = False,
    chunks_per_source: Optional[int] = None,
    auto_parameters: bool = False,
    topic: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> dict:
    api_key = getattr(plugin_config.search, "tavily_api_key", None)
    if not api_key:
        raise Exception(
            "未配置 tavily_api_key，请在 config/plugins/ai_assistant/config.json 中填写。"
        )

    depth = (
        search_depth
        if search_depth is not None
        else (getattr(plugin_config.search, "depth", "basic") or "basic")
    )
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": int(
            max_results
            if max_results is not None
            else (getattr(plugin_config.search, "max_results", 5) or 5)
        ),
        "search_depth": depth,
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
        "include_images": bool(include_images),
        "include_image_descriptions": bool(include_image_descriptions),
    }

    if chunks_per_source is not None and depth == "advanced":
        payload["chunks_per_source"] = max(1, min(3, int(chunks_per_source)))
    if auto_parameters:
        payload["auto_parameters"] = True
    if topic:
        payload["topic"] = topic
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
    normalized_results: List[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        normalized = _normalization._normalize_full_result(item)
        if (
            not normalized.get("url")
            and not normalized.get("content")
            and not normalized.get("title")
        ):
            continue
        normalized_results.append(normalized)

    return {
        "query": _normalization._strip_control_chars(data.get("query") or query),
        "answer": _normalization._strip_control_chars(data.get("answer") or ""),
        "images": _normalization._normalize_images(data.get("images")),
        "results": normalized_results,
        "auto_parameters": data.get("auto_parameters"),
        "response_time": data.get("response_time"),
        "usage": data.get("usage"),
        "request_id": data.get("request_id"),
    }


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
    data = await tavily_search_full(
        query,
        max_results=max_results,
        search_depth=search_depth,
        include_answer=False,
        include_raw_content=False,
        include_images=False,
        include_image_descriptions=False,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "content": item.get("content") or "",
        }
        for item in data.get("results", [])
    ]
