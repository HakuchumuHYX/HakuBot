import httpx
import json
import re
import math
from typing import Any, Tuple, Optional, List
from nonebot.log import logger
from pydantic import Field
from plugins.ai_assistant.config import StrictBaseModel, plugin_config
from plugins.ai_assistant.services.chat_service import call_chat_completion
from plugins.ai_assistant.services.search import client as _client
from plugins.ai_assistant.services.search import queries as _queries


async def web_search_with_rewrite(
    raw_text: str, *, mode: str = "chat"
) -> Tuple[List[str], List[dict]]:
    """
    统一入口：对 raw_text 做 query 提炼/重写 → 多 query 搜索 → 合并去重。
    返回：(queries, merged_results)
    """
    queries = await _queries._resolve_search_queries(raw_text, mode=mode)

    total_max = int(getattr(plugin_config.search, "max_results", 5) or 5)
    if total_max < 1:
        total_max = 5

    per_query = max(1, int(math.ceil(total_max / max(1, len(queries)))))

    merged_results: List[dict] = []
    seen_urls = set()

    for q in queries:
        try:
            rs = await _client.tavily_search(q, max_results=per_query)
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


async def web_chat_search_with_rewrite(
    raw_text: str,
    *,
    max_results: Optional[int] = None,
    search_depth: Optional[str] = None,
    include_answer: Any = None,
    include_raw_content: Any = None,
    chunks_per_source: Optional[int] = None,
    auto_parameters: Optional[bool] = None,
) -> Tuple[List[str], List[dict]]:
    queries = await _queries._resolve_search_queries(raw_text, mode="chat")

    total_max = int(
        max_results
        if max_results is not None
        else (getattr(plugin_config.search, "chat_max_results", 5) or 5)
    )
    if total_max < 1:
        total_max = 5
    per_query = max(1, int(math.ceil(total_max / max(1, len(queries)))))

    payloads: List[dict] = []
    seen_urls = set()
    collected_results = 0

    for q in queries:
        depth = (
            search_depth
            if search_depth is not None
            else (getattr(plugin_config.search, "chat_depth", "basic") or "basic")
        )
        try:
            data = await _client.tavily_search_full(
                q,
                max_results=per_query,
                search_depth=depth,
                include_answer=include_answer
                if include_answer is not None
                else getattr(plugin_config.search, "chat_include_answer", "basic"),
                include_raw_content=include_raw_content
                if include_raw_content is not None
                else getattr(plugin_config.search, "chat_include_raw_content", False),
                include_images=False,
                include_image_descriptions=False,
                chunks_per_source=int(
                    chunks_per_source
                    if chunks_per_source is not None
                    else (
                        getattr(plugin_config.search, "chat_chunks_per_source", 1) or 1
                    )
                ),
                auto_parameters=bool(
                    auto_parameters
                    if auto_parameters is not None
                    else getattr(plugin_config.search, "chat_auto_parameters", False)
                ),
                topic="general",
            )
        except Exception as e:
            logger.warning(f"Tavily Chat 搜索失败: query={q} err={e}")
            continue

        deduped_results: List[dict] = []
        for item in data.get("results", []):
            url = (item.get("url") or "").strip()
            key = url or (item.get("title") or "") + (item.get("content") or "")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            deduped_results.append(item)
            collected_results += 1
            if collected_results >= total_max:
                break

        data["results"] = deduped_results
        payloads.append(data)
        if collected_results >= total_max:
            break

    return queries, payloads


async def web_image_search_with_rewrite(raw_text: str) -> Tuple[List[str], List[dict]]:
    queries = await _queries._resolve_search_queries(raw_text, mode="image")

    total_max = int(getattr(plugin_config.search, "image_max_results", 5) or 5)
    if total_max < 1:
        total_max = 5
    per_query = max(1, int(math.ceil(total_max / max(1, len(queries)))))

    payloads: List[dict] = []
    seen_urls = set()
    seen_images = set()
    collected_results = 0

    for q in queries:
        try:
            data = await _client.tavily_search_full(
                q,
                max_results=per_query,
                search_depth=getattr(plugin_config.search, "image_depth", "advanced")
                or "advanced",
                include_answer=getattr(
                    plugin_config.search, "image_include_answer", "basic"
                ),
                include_raw_content=getattr(
                    plugin_config.search, "image_include_raw_content", "text"
                ),
                include_images=bool(
                    getattr(plugin_config.search, "image_include_images", True)
                ),
                include_image_descriptions=bool(
                    getattr(
                        plugin_config.search, "image_include_image_descriptions", True
                    )
                ),
                chunks_per_source=int(
                    getattr(plugin_config.search, "image_chunks_per_source", 1) or 1
                ),
                auto_parameters=bool(
                    getattr(plugin_config.search, "image_auto_parameters", False)
                ),
                topic="general",
            )
        except Exception as e:
            logger.warning(f"Tavily 生图搜索失败: query={q} err={e}")
            continue

        deduped_results: List[dict] = []
        for item in data.get("results", []):
            url = (item.get("url") or "").strip()
            key = url or (item.get("title") or "") + (item.get("content") or "")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            deduped_results.append(item)
            collected_results += 1
            if collected_results >= total_max:
                break

        deduped_images: List[dict] = []
        for image in data.get("images", []):
            key = image.get("url") or image.get("description")
            if not key or key in seen_images:
                continue
            seen_images.add(key)
            deduped_images.append(image)

        data["results"] = deduped_results
        data["images"] = deduped_images
        payloads.append(data)
        if collected_results >= total_max:
            break

    return queries, payloads
