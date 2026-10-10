import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import quote

from PicImageSearch import Network, SauceNAO

from plugins.lunabot_imgexp.config import config
from utils.logging import get_logger
from utils.network.http import (
    DEFAULT_TIMEOUT,
    get_client_session,
    get_effective_proxy,
)
from utils.network.images import fetch_image
from utils.rendering.engine import render_template

logger = get_logger("ImgExp")
TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass
class ImageSearchResultItem:
    title: str
    url: str
    source: Optional[str] = None
    # 图标与缩略图是 data URI，直接嵌进结果模板
    source_icon: Optional[str] = None
    similarity: Optional[float] = None
    thumbnail: Optional[str] = None


@dataclass
class ImageSearchResult:
    source: str
    results: Optional[list[ImageSearchResultItem]] = None
    error: Optional[str] = None


# 缩略图下载并发限制：避免一次性开太多连接导致排队/卡死
_THUMB_SEM = asyncio.Semaphore(8)
# 给“单张缩略图”单独设置更短的硬超时，超时就当没有缩略图，不影响主结果返回。
_THUMB_TIMEOUT_SEC = 30


async def download_batch_thumbnails(urls: list[str]) -> list[Optional[str]]:
    plugin_proxy = config.get("proxy")

    async def download_nothrow(url: str):
        if not url:
            return None
        async with _THUMB_SEM:
            try:
                image = await asyncio.wait_for(
                    fetch_image(url, proxy=plugin_proxy, timeout=_THUMB_TIMEOUT_SEC),
                    timeout=_THUMB_TIMEOUT_SEC,
                )
                data = base64.b64encode(image.data).decode()
                return f"data:{image.mime_type};base64,{data}"
            except asyncio.TimeoutError:
                logger.warning(f"下载缩略图超时({_THUMB_TIMEOUT_SEC}s): {url}")
                return None
            except Exception as e:
                logger.warning(f"下载缩略图 {url} 失败: {e}")
                return None

    return await asyncio.gather(*[download_nothrow(url) for url in urls])


async def _with_timeout(coro, source: str, timeout_sec: int = 120) -> ImageSearchResult:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.warning(f"{source} 搜索超时: {timeout_sec}s")
        return ImageSearchResult(
            source=source, results=[], error=f"搜索超时({timeout_sec}s)"
        )
    except Exception as e:
        logger.warning(f"{source} 搜索失败: {e}")
        return ImageSearchResult(source=source, results=[], error=f"搜索失败: {e}")


async def search_saucenao(
    client: Any,
    img_url: str,
    limit: int = 10,
    min_similarity: int = 60,
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source="SauceNAO", results=[])
        logger.info("开始从 SauceNAO 搜索图片...")

        saucenao = SauceNAO(
            client=client,
            api_key=config.get("saucenao_apikey"),
            numres=limit,
        )
        results = await saucenao.search(url=img_url)

        results = [
            item
            for item in results.raw
            if item.similarity >= min_similarity and item.url and item.thumbnail
        ]

        thumbnails = await download_batch_thumbnails(
            [item.thumbnail for item in results]
        )
        items = [
            ImageSearchResultItem(
                title=item.title,
                url=item.url,
                similarity=item.similarity,
                thumbnail=thumbnail,
            )
            for item, thumbnail in zip(results, thumbnails)
        ]
        items.sort(key=lambda x: x.similarity or 0, reverse=True)
        logger.info(f"从 SauceNAO 搜索到 {len(items)} 个结果")
        return ImageSearchResult(source="SauceNAO", results=items)

    except Exception as e:
        logger.warning(f"从 SauceNAO 搜索图片 {img_url} 失败: {e}")
        return ImageSearchResult(source="SauceNAO", results=[], error=f"搜索失败: {e}")


async def search_googlelens(
    img_url: str,
    limit: int = 10,
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source="GoogleLens", results=[])
        logger.info("开始从 GoogleLens 搜索图片...")

        serp_apikey = config.get("serp_apikey")
        if not serp_apikey:
            logger.warning("未配置 SerpApi Key，跳过 GoogleLens 搜索")
            return ImageSearchResult(
                source="GoogleLens", results=[], error="未配置 SerpApi Key"
            )

        img_url_encoded = quote(img_url, safe="")
        serp_url = f"https://serpapi.com/search.json?engine=google_lens&url={img_url_encoded}&api_key={serp_apikey}"

        proxy = get_effective_proxy(config.get("proxy"))
        async with get_client_session().get(
            serp_url,
            proxy=proxy,
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(
                    f"HTTP {response.status} {response.reason}: {error_text}"
                )

            data = await response.json()
            if "error" in data:
                raise Exception(f"SerpApi Error: {data['error']}")

            results = data.get("visual_matches", [])
            results = [
                item for item in results if item.get("title") and item.get("link")
            ][:limit]

            source_icon_urls = [item.get("source_icon") for item in results]
            source_icons = await download_batch_thumbnails(source_icon_urls)

            thumbnail_urls = [item.get("thumbnail") for item in results]
            thumbnails = await download_batch_thumbnails(thumbnail_urls)

            items = [
                ImageSearchResultItem(
                    title=item["title"],
                    url=item["link"],
                    source=item.get("source"),
                    source_icon=source_icon,
                    thumbnail=thumbnail,
                )
                for item, source_icon, thumbnail in zip(
                    results, source_icons, thumbnails
                )
            ]

            logger.info(f"从 GoogleLens 搜索到 {len(items)} 个结果")
            return ImageSearchResult(source="GoogleLens", results=items)

    except Exception as e:
        logger.warning(f"从 GoogleLens 搜索图片 {img_url} 失败: {e}")
        return ImageSearchResult(
            source="GoogleLens", results=[], error=f"搜索失败: {e}"
        )


async def search_image(
    img_url: str,
    img_size: int = 0,  # img_size 默认为0，如果不提供
) -> Tuple[bytes, List[ImageSearchResult]]:
    SIZE_LIMIT_MB = 15
    if img_size > SIZE_LIMIT_MB * 1024 * 1024:
        raise ValueError(f"图片大小超过{SIZE_LIMIT_MB}MB，请先缩小后再进行搜索")

    proxy = get_effective_proxy(config.get("proxy"))

    async with Network(timeout=120, proxies=proxy) as client:
        # 并行执行所有搜索（已移除 TraceMoe）
        results = await asyncio.gather(
            _with_timeout(search_saucenao(client, img_url), "SauceNAO"),
            _with_timeout(search_googlelens(img_url), "GoogleLens"),
        )

    image = await render_template(
        TEMPLATE_DIR,
        "search.html",
        width=900,
        results=results,
        watermark_text=config.get("watermark_text", "").strip(),
    )
    return image, results
