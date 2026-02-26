import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from urllib.parse import quote

from PIL import Image
from PicImageSearch import Network, SauceNAO

from .config import config
from ..utils.tools import get_logger
from ..utils.network import DEFAULT_TIMEOUT, download_image, get_client_session, get_effective_proxy
from ..utils.draw.painter import *
from ..utils.draw.plot import *

logger = get_logger("ImgExp")


@dataclass
class ImageSearchResultItem:
    title: str
    url: str
    source: Optional[str] = None
    source_icon: Optional[Image.Image] = None
    similarity: Optional[float] = None
    thumbnail: Optional[Image.Image] = None


@dataclass
class ImageSearchResult:
    source: str
    results: Optional[list[ImageSearchResultItem]] = None
    error: Optional[str] = None


# 缩略图下载并发限制：避免一次性开太多连接导致排队/卡死
_THUMB_SEM = asyncio.Semaphore(8)
# 给“单张缩略图”单独设置更短的硬超时，超时就当没有缩略图，不影响主结果返回。
_THUMB_TIMEOUT_SEC = 30


async def download_batch_thumbnails(urls: list[str]) -> list[Image.Image]:
    plugin_proxy = config.get("proxy")

    async def download_nothrow(url: str):
        if not url:
            return None
        async with _THUMB_SEM:
            try:
                return await asyncio.wait_for(
                    download_image(url, proxy=plugin_proxy),
                    timeout=_THUMB_TIMEOUT_SEC,
                )
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
        return ImageSearchResult(source=source, results=[], error=f"搜索超时({timeout_sec}s)")
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

        thumbnails = await download_batch_thumbnails([item.thumbnail for item in results])
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
            return ImageSearchResult(source="GoogleLens", results=[], error="未配置 SerpApi Key")

        img_url_encoded = quote(img_url, safe="")
        serp_url = (
            f"https://serpapi.com/search.json?engine=google_lens&url={img_url_encoded}&api_key={serp_apikey}"
        )

        proxy = get_effective_proxy(config.get("proxy"))
        async with get_client_session().get(
            serp_url,
            verify_ssl=False,
            proxy=proxy,
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"HTTP {response.status} {response.reason}: {error_text}")

            data = await response.json()
            if "error" in data:
                raise Exception(f"SerpApi Error: {data['error']}")

            results = data.get("visual_matches", [])
            results = [item for item in results if item.get("title") and item.get("link")][:limit]

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
                for item, source_icon, thumbnail in zip(results, source_icons, thumbnails)
            ]

            logger.info(f"从 GoogleLens 搜索到 {len(items)} 个结果")
            return ImageSearchResult(source="GoogleLens", results=items)

    except Exception as e:
        logger.warning(f"从 GoogleLens 搜索图片 {img_url} 失败: {e}")
        return ImageSearchResult(source="GoogleLens", results=[], error=f"搜索失败: {e}")


async def search_image(
    img_url: str,
    img_size: int = 0,  # img_size 默认为0，如果不提供
) -> Tuple[Image.Image, List[ImageSearchResult]]:
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

    # Drawing Logic
    bg = FillBg(
        LinearGradient(c1=(220, 220, 255, 255), c2=(220, 240, 255, 255), p1=(0, 0), p2=(1, 1))
    )
    item_bg = RoundRectBg((255, 255, 255, 125), 10, blurglass=True)
    text_color1 = (50, 50, 50, 255)
    text_color2 = (75, 75, 75, 255)
    w = 800

    canvas = Canvas(bg=bg).set_padding(10)
    with canvas:
        with (
            VSplit()
            .set_sep(16)
            .set_padding(16)
            .set_item_bg(item_bg)
            .set_content_align("l")
            .set_item_align("l")
        ):
            for result in results:
                with VSplit().set_sep(16).set_padding(16).set_content_align("l").set_item_align("l"):
                    TextBox(
                        f"来自 {result.source} 的结果",
                        style=TextStyle(font=DEFAULT_BOLD_FONT, size=35, color=text_color1),
                    )
                    if result.error:
                        TextBox(
                            result.error,
                            style=TextStyle(font=DEFAULT_FONT, size=24, color=RED),
                            use_real_line_count=True,
                        ).set_w(w)
                    else:
                        if not result.results:
                            TextBox(
                                "未找到结果",
                                style=TextStyle(font=DEFAULT_FONT, size=32, color=text_color2),
                            ).set_margin(32)
                        else:
                            result_container = (
                                VSplit()
                                .set_sep(8)
                                .set_padding(16)
                                .set_item_bg(item_bg)
                                .set_content_align("l")
                                .set_item_align("l")
                            )
                            with result_container:
                                for i, item in enumerate(result.results):
                                    with HSplit().set_sep(8).set_item_align("l").set_content_align("l"):
                                        if item.thumbnail:
                                            size = 150
                                            frame = Frame().set_margin(32).set_content_align("c").set_size(
                                                (size, size)
                                            )
                                            with frame:
                                                thumb_copy = item.thumbnail.copy()
                                                thumb_copy.thumbnail((size, size))
                                                ImageBox(thumb_copy)
                                        with VSplit().set_sep(12).set_item_align("l").set_content_align("l"):
                                            with HSplit().set_sep(6).set_item_align("l").set_content_align("l"):
                                                TextBox(
                                                    f"#{i + 1}",
                                                    style=TextStyle(
                                                        font=DEFAULT_BOLD_FONT, size=32, color=text_color1
                                                    ),
                                                )
                                                Spacer(w=8)
                                                if item.source:
                                                    TextBox(
                                                        f"From {item.source}",
                                                        style=TextStyle(
                                                            font=DEFAULT_FONT, size=24, color=text_color2
                                                        ),
                                                    )
                                                if item.source_icon:
                                                    ImageBox(item.source_icon, size=(None, 24)).set_offset((0, 4))
                                                if item.similarity is not None:
                                                    TextBox(
                                                        f"相似度: {item.similarity:.2f}%",
                                                        style=TextStyle(
                                                            font=DEFAULT_FONT, size=24, color=text_color2
                                                        ),
                                                    )
                                            if item.title:
                                                TextBox(
                                                    item.title,
                                                    style=TextStyle(
                                                        font=DEFAULT_BOLD_FONT, size=28, color=text_color1
                                                    ),
                                                ).set_w(w)
                                            if item.url:
                                                TextBox(
                                                    item.url,
                                                    style=TextStyle(
                                                        font=DEFAULT_FONT, size=24, color=text_color2
                                                    ),
                                                ).set_w(w)

    img = await canvas.get_img()

    # 绘制水印
    watermark_text = config.get("watermark_text", "")
    if watermark_text:
        p = Painter(img)
        font_size = 20
        font = get_font(DEFAULT_BOLD_FONT, font_size)
        lines = watermark_text.strip().split("\n")
        line_height = font_size + 4
        padding = 15

        total_height = len(lines) * line_height
        start_y = img.height - total_height - padding

        cur_y = start_y
        for line in lines:
            if not line:
                cur_y += line_height
                continue

            w_text, _h_text = get_text_size(font, line)
            x = img.width - w_text - padding

            # 阴影
            p.text(line, (x + 2, cur_y + 2), font, fill=(0, 0, 0, 128))
            # 主体
            p.text(line, (x, cur_y), font, fill=(255, 255, 255, 180))

            cur_y += line_height

        img = await p.get()

    return img, results
