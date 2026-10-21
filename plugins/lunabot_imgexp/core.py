import asyncio
import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import quote, urlencode

import aiohttp
from nonebot import logger
from PicImageSearch import Network, SauceNAO
from PicImageSearch.model import Ascii2DResponse

from plugins.lunabot_imgexp.config import config
from utils.network.http import (
    DEFAULT_TIMEOUT,
    DownloadTooLarge,
    download_bytes,
    get_client_session,
    get_effective_proxy,
)
from utils.network.images import fetch_image
from utils.rendering.engine import render_template

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
    plugin_proxy = config.proxy

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


def _saucenao_link(item: Any) -> Tuple[str, Optional[str]]:
    """booru 类结果的 ext_urls 只是 booru 帖子页，人工填写的 source 才是原始出处，优先用它。"""
    source = str(item.origin["data"].get("source") or "")
    if not source.startswith("http"):
        return item.url, None
    # SauceNAO 会截断过长的 source，pximg 直链可能只剩到作品 id
    pximg = re.search(r"pximg\.net/.+/(\d{5,})", source)
    if pximg:
        source = f"https://www.pixiv.net/artworks/{pximg.group(1)}"
    # index_name 形如 "Index #12: Yande.re - xxx.jpg"
    via = item.index_name.split(": ", 1)[-1].rsplit(" - ", 1)[0]
    return source, via


def _work_key(url: str) -> str:
    """同一推文/作品的不同写法（带用户名、i/web 形式等）归为一个键。"""
    tweet = re.search(r"(?:twitter|x)\.com/.+/status/(\d+)", url)
    if tweet:
        return f"tweet:{tweet.group(1)}"
    pixiv = re.search(r"pixiv\.net/(?:artworks/|.*illust_id=)(\d+)", url)
    if pixiv:
        return f"pixiv:{pixiv.group(1)}"
    return url


async def search_saucenao(
    client: Any,
    img_bytes: bytes,
    limit: int = 10,
    min_similarity: int = 60,
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source="SauceNAO", results=[])
        logger.debug("开始从 SauceNAO 搜索图片...")

        saucenao = SauceNAO(
            client=client,
            api_key=config.saucenao_apikey,
            numres=limit,
        )
        # 直接上传图片，不依赖 SauceNAO 能否回源拉到带时效 rkey 的 QQ 图链
        results = await saucenao.search(file=img_bytes)

        # 转载号与原作者的相似度往往几乎相同：同一作品只保留最高分，并显示作者便于分辨
        best = {}
        for item in results.raw:
            if item.similarity < min_similarity:
                continue
            url, via = _saucenao_link(item)
            key = _work_key(url)
            if url and (key not in best or item.similarity > best[key][1].similarity):
                best[key] = (url, item, via)
        picked = sorted(best.values(), key=lambda x: x[1].similarity, reverse=True)

        thumbnails = await download_batch_thumbnails(
            [item.thumbnail for _, item, _ in picked]
        )
        items = [
            ImageSearchResultItem(
                title=item.title,
                url=url,
                source=" · ".join(filter(None, [item.author, via and f"经 {via} 溯源"]))
                or None,
                similarity=item.similarity,
                thumbnail=thumbnail,
            )
            for (url, item, via), thumbnail in zip(picked, thumbnails)
        ]
        logger.debug(f"从 SauceNAO 搜索到 {len(items)} 个结果")
        return ImageSearchResult(source="SauceNAO", results=items)

    except Exception as e:
        logger.warning(f"从 SauceNAO 搜索图片失败: {e}")
        return ImageSearchResult(source="SauceNAO", results=[], error=f"搜索失败: {e}")


async def _flaresolverr(http: aiohttp.ClientSession, **payload) -> dict:
    async with http.post(
        config.flaresolverr_url, json={"maxTimeout": 30000, **payload}
    ) as resp:
        data = await resp.json()
    if data.get("status") != "ok":
        raise Exception(f"FlareSolverr: {data.get('message')}")
    return data


async def search_ascii2d(img_url: str, limit: int = 3) -> ImageSearchResult:
    try:
        if not config.flaresolverr_url:
            return ImageSearchResult(
                source="Ascii2D", results=[], error="未配置 FlareSolverr"
            )
        logger.debug("开始从 Ascii2D 搜索图片...")

        # Ascii2D 在 Cloudflare 后面，拿到 clearance 后上传文件接口仍会被质询，
        # 只能让 FlareSolverr 的浏览器走按链接搜索（color 检索）
        async with aiohttp.ClientSession() as http:
            sid = (await _flaresolverr(http, cmd="sessions.create"))["session"]
            try:
                home = await _flaresolverr(
                    http, cmd="request.get", url="https://ascii2d.net/", session=sid
                )
                token = re.search(
                    r'name="authenticity_token" value="([^"]+)"',
                    home["solution"]["response"],
                ).group(1)
                result = await _flaresolverr(
                    http,
                    cmd="request.post",
                    url="https://ascii2d.net/search/uri",
                    postData=urlencode(
                        {"utf8": "✓", "authenticity_token": token, "uri": img_url}
                    ),
                    session=sid,
                )
            finally:
                await _flaresolverr(http, cmd="sessions.destroy", session=sid)

        solution = result["solution"]
        # 第一项是查询图本身；没有外链的结果没有参考价值，同一链接常重复出现
        unique = {}
        for item in Ascii2DResponse(solution["response"], solution["url"]).raw[1:]:
            if item.url:
                unique.setdefault(item.url, item)
        results = list(unique.values())[:limit]

        thumbnails = await download_batch_thumbnails(
            [item.thumbnail for item in results]
        )
        items = [
            ImageSearchResultItem(
                title=item.title,
                url=item.url,
                source=item.author or None,
                thumbnail=thumbnail,
            )
            for item, thumbnail in zip(results, thumbnails)
        ]
        logger.debug(f"从 Ascii2D 搜索到 {len(items)} 个结果")
        return ImageSearchResult(source="Ascii2D", results=items)

    except Exception as e:
        logger.warning(f"从 Ascii2D 搜索图片 {img_url} 失败: {e}")
        return ImageSearchResult(source="Ascii2D", results=[], error=f"搜索失败: {e}")


async def search_googlelens(
    img_url: str,
    limit: int = 10,
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source="GoogleLens", results=[])
        logger.debug("开始从 GoogleLens 搜索图片...")

        serp_apikey = config.serp_apikey
        if not serp_apikey:
            logger.warning("未配置 SerpApi Key，跳过 GoogleLens 搜索")
            return ImageSearchResult(
                source="GoogleLens", results=[], error="未配置 SerpApi Key"
            )

        img_url_encoded = quote(img_url, safe="")
        serp_url = f"https://serpapi.com/search.json?engine=google_lens&url={img_url_encoded}&api_key={serp_apikey}"

        proxy = get_effective_proxy(config.proxy)
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

            logger.debug(f"从 GoogleLens 搜索到 {len(items)} 个结果")
            return ImageSearchResult(source="GoogleLens", results=items)

    except Exception as e:
        logger.warning(f"从 GoogleLens 搜索图片 {img_url} 失败: {e}")
        return ImageSearchResult(
            source="GoogleLens", results=[], error=f"搜索失败: {e}"
        )


async def search_image(img_url: str) -> Tuple[bytes, List[ImageSearchResult]]:
    SIZE_LIMIT_MB = 15
    proxy = get_effective_proxy(config.proxy)
    try:
        img_bytes = await download_bytes(
            img_url, proxy=config.proxy, max_bytes=SIZE_LIMIT_MB * 1024 * 1024
        )
    except DownloadTooLarge:
        raise ValueError(f"图片大小超过{SIZE_LIMIT_MB}MB，请先缩小后再进行搜索")

    async with Network(timeout=120, proxies=proxy) as client:
        results = await asyncio.gather(
            _with_timeout(search_saucenao(client, img_bytes), "SauceNAO"),
            # Cloudflare 偶尔要长时间验证，单独限时，别拖慢整体出结果
            _with_timeout(search_ascii2d(img_url), "Ascii2D", 45),
            _with_timeout(search_googlelens(img_url), "GoogleLens"),
        )

    image = await render_template(
        TEMPLATE_DIR,
        "search.html",
        width=900,
        query_url=img_url,
        results=results,
        watermark_text=config.watermark_text.strip(),
    )
    return image, results
