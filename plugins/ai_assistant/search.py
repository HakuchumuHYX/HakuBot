"""联网搜索：DeepSeek 出网页结果，生图联网时 Tavily 补图片（DeepSeek 搜索不返回图片）；
没填 DeepSeek key 时全部走 Tavily"""

import asyncio
import re
from dataclasses import dataclass

import httpx

from plugins.ai_assistant.config import plugin_config

DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
SNIPPET_MAX_CHARS = 600

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "联网搜索，返回若干网页的标题、链接和摘要。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，简短具体，不超过 128 字",
                }
            },
            "required": ["query"],
        },
    },
}


@dataclass
class SearchHit:
    title: str
    url: str
    content: str


@dataclass
class SearchImage:
    url: str
    description: str


@dataclass
class SearchResult:
    hits: list[SearchHit]
    images: list[SearchImage]


def _clean(text: str | None) -> str:
    return re.sub(r"[\x00-\x1f\x7f\s]+", " ", text or "").strip()


async def _deepseek_hits(client: httpx.AsyncClient, query: str) -> list[SearchHit]:
    resp = await client.post(
        DEEPSEEK_SEARCH_URL,
        json={"query": query, "count": MAX_RESULTS},
        headers={"Authorization": f"Bearer {plugin_config.deepseek_api_key}"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:200]}")
    # summary 是 DeepSeek 专门给模型看的摘要，比 snippet 长
    return [
        SearchHit(
            title=_clean(item.get("title")),
            url=item.get("url") or "",
            content=_clean(item.get("summary"))[:SNIPPET_MAX_CHARS],
        )
        for item in resp.json().get("results") or []
    ]


async def _tavily_search(
    client: httpx.AsyncClient, query: str, *, max_results: int, include_images: bool
) -> SearchResult:
    payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_images": include_images,
        "include_image_descriptions": include_images,
    }
    resp = await client.post(
        TAVILY_URL,
        json=payload,
        headers={"Authorization": f"Bearer {plugin_config.tavily_api_key}"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    hits = [
        SearchHit(
            title=_clean(item.get("title")),
            url=item.get("url") or "",
            content=_clean(item.get("content"))[:SNIPPET_MAX_CHARS],
        )
        for item in data.get("results") or []
    ]
    # 打开 include_image_descriptions 时，images 每项是 {"url", "description"}
    images = [
        SearchImage(url=item["url"], description=_clean(item.get("description")))
        for item in data.get("images") or []
    ]
    return SearchResult(hits=hits, images=images)


async def web_search(query: str, *, include_images: bool) -> SearchResult:
    async with httpx.AsyncClient(
        proxy=plugin_config.proxy, timeout=plugin_config.timeout
    ) as client:
        # 没填 DeepSeek key 时整套走 Tavily
        if not plugin_config.deepseek_api_key:
            return await _tavily_search(
                client, query, max_results=MAX_RESULTS, include_images=include_images
            )
        if not include_images:
            return SearchResult(hits=await _deepseek_hits(client, query), images=[])
        # 网页结果由 DeepSeek 出，Tavily 只要图片；images 数量不随 max_results 变
        hits, tavily = await asyncio.gather(
            _deepseek_hits(client, query),
            _tavily_search(client, query, max_results=1, include_images=True),
        )
    return SearchResult(hits=hits, images=tavily.images)


def format_results(
    result: SearchResult, image_labels: dict[str, SearchImage] | None = None
) -> str:
    """作为工具结果交给模型的文本。

    image_labels 是整次请求共用的「编号 → 图片」表，新图片接着编号写进去；
    不传则不列图片。
    """
    if not result.hits and not result.images:
        return "没有搜到结果。"

    blocks = [
        f"[{index}] {hit.title}\n{hit.url}\n{hit.content}"
        for index, hit in enumerate(result.hits, start=1)
    ]
    if image_labels is not None and result.images:
        known = {image.url: label for label, image in image_labels.items()}
        lines = ["候选图片："]
        for image in result.images:
            label = known.get(image.url)
            if label is None:
                label = f"I{len(image_labels) + 1}"
                image_labels[label] = image
                known[image.url] = label
            lines.append(f"[{label}] {image.description or '（无描述）'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
