import asyncio
from typing import List, Tuple, Optional
from dataclasses import dataclass
from urllib.parse import quote
from PIL import Image
from PicImageSearch import SauceNAO, Network

from .utils.config import config
from .utils.tools import get_logger
from .utils.network import get_client_session, download_image
from .draw.painter import *
from .draw.plot import *

logger = get_logger('ImgExp')


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


async def download_batch_thumbnails(urls: list[str]) -> list[Image.Image]:
    async def download_nothrow(url):
        if not url: return None
        try:
            return await download_image(url)
        except Exception as e:
            logger.warning(f'下载缩略图 {url} 失败: {e}')
            return None
    return await asyncio.gather(*[download_nothrow(url) for url in urls])
    

async def search_saucenao(
    img_url: str, 
    limit: int = 10,
    min_similarity: int = 60
) -> ImageSearchResult:
    try:
        if limit == 0: 
            return ImageSearchResult(source='SauceNAO', results=[])
        logger.info("开始从SauceNAO搜索图片...")
        
        proxy = config.get('proxy')
        proxies = f"http://{proxy}" if proxy else None
        
        async with Network(timeout=20, proxies=proxies) as client:
            saucenao = SauceNAO(client=client, api_key=config.get('saucenao_apikey'), numres=limit)
            results = await saucenao.search(url=img_url)
            results = [
                item for item in results.raw 
                if item.similarity >= min_similarity and item.url and item.thumbnail
            ]
            thumbnails = await download_batch_thumbnails([item.thumbnail for item in results])
            results = [ImageSearchResultItem(
                title=item.title,
                url=item.url,
                similarity=item.similarity,
                thumbnail=thumbnail
            ) for item, thumbnail in zip(results, thumbnails)]
            results.sort(key=lambda x: x.similarity, reverse=True)
            logger.info(f"从SauceNAO搜索到 {len(results)} 个结果")
            return ImageSearchResult(source='SauceNAO', results=results)
        
    except Exception as e:
        logger.warning(f'从SauceNAO搜索图片 {img_url} 失败: {e}')
        # logger.print_exc(f'从SauceNAO搜索图片 {img_url} 失败') # logger wrapper may not support print_exc
        import traceback
        traceback.print_exc()
        return ImageSearchResult(source='SauceNAO', results=[], error=f"搜索失败: {e}")


async def search_googlelens(
    img_url: str,
    limit: int = 10,
) -> list[ImageSearchResultItem]:
    try:
        if limit == 0:
            return ImageSearchResult(source='GoogleLens', results=[])
        logger.info("开始从GoogleLens搜索图片...")

        serp_apikey = config.get('serp_apikey')
        if not serp_apikey:
             logger.warning("未配置 SerpApi Key，跳过 GoogleLens 搜索")
             return ImageSearchResult(source='GoogleLens', results=[], error="未配置 SerpApi Key")

        img_url_encoded = quote(img_url, safe='')
        serp_url = f'https://serpapi.com/search.json?engine=google_lens&url={img_url_encoded}&api_key={serp_apikey}'
        
        # 使用自定义 session，可能配置了代理
        async with get_client_session().get(serp_url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"HTTP {response.status} {response.reason}: {error_text}")
            
            data = await response.json()
            if "error" in data:
                 raise Exception(f"SerpApi Error: {data['error']}")
            
            results = data.get('visual_matches', [])
            results = [item for item in results if item.get('title') and item.get('link')][:limit]

            source_icon_urls = [item.get('source_icon') for item in results]
            source_icons = await download_batch_thumbnails(source_icon_urls)

            thumbnail_urls = [item.get('thumbnail') for item in results]
            thumbnails = await download_batch_thumbnails(thumbnail_urls)

            results = [ImageSearchResultItem(
                title=item['title'],
                url=item['link'],
                source=item.get('source'),
                source_icon=source_icon,
                thumbnail=thumbnail,
            ) for item, source_icon, thumbnail in zip(results, source_icons, thumbnails)]

            logger.info(f"从GoogleLens搜索到 {len(results)} 个结果")
            return ImageSearchResult(source='GoogleLens', results=results)
    
    except Exception as e:
        logger.warning(f'从GoogleLens搜索图片 {img_url} 失败: {e}')
        import traceback
        traceback.print_exc()
        return ImageSearchResult(source='GoogleLens', results=[], error=f"搜索失败: {e}")


async def search_image(
    img_url: str,
    img_size: int = 0, # img_size 默认为0，如果不提供
) -> Tuple[Image.Image, List[ImageSearchResult]]:
    SIZE_LIMIT_MB = 15
    if img_size > SIZE_LIMIT_MB * 1024 * 1024:
        raise ValueError(f"图片大小超过{SIZE_LIMIT_MB}MB，请先缩小后再进行搜索")

    saucenao_result = await search_saucenao(img_url)
    
    # 如果SauceNAO有高相似度结果(>=90%)，可以跳过Google Lens以节省配额/时间
    # 但如果用户确实想看Google Lens结果，这里可能需要调整策略。暂时保持原逻辑。
    if saucenao_result.results and saucenao_result.results[0].similarity and saucenao_result.results[0].similarity >= 90:
        logger.info(f"从SauceNAO搜索到高相似度图片 ({saucenao_result.results[0].similarity}%)，跳过GoogleLens搜索")
        results = [saucenao_result]
    else:
        google_result = await search_googlelens(img_url)
        results = [saucenao_result, google_result]
    
    bg = FillBg(LinearGradient(c1=(220, 220, 255, 255), c2=(220, 240, 255, 255), p1=(0, 0), p2=(1, 1)))
    item_bg = RoundRectBg((255, 255, 255, 125), 10, blurglass=True)
    text_color1 = (50, 50, 50, 255)
    text_color2 = (75, 75, 75, 255)
    w = 800
    
    # 绘图逻辑
    canvas = Canvas(bg=bg).set_padding(10)
    with canvas:
        with VSplit().set_sep(16).set_padding(16).set_item_bg(item_bg).set_content_align('l').set_item_align('l'):
            for result in results:
                with VSplit().set_sep(16).set_padding(16).set_content_align('l').set_item_align('l'):
                    TextBox(f"来自 {result.source} 的结果", style=TextStyle(font=DEFAULT_BOLD_FONT, size=35, color=text_color1))
                    if result.error:
                        TextBox(result.error, style=TextStyle(font=DEFAULT_FONT, size=24, color=RED), use_real_line_count=True).set_w(w)
                    else:
                        if not result.results:
                            TextBox("未找到结果", style=TextStyle(font=DEFAULT_FONT, size=32, color=text_color2)).set_margin(32)
                        else:
                            # 结果列表容器
                            result_container = VSplit().set_sep(8).set_padding(16).set_item_bg(item_bg).set_content_align('l').set_item_align('l')
                            with result_container:
                                for i, item in enumerate(result.results):
                                    with HSplit().set_sep(8).set_item_align('l').set_content_align('l'):
                                        if item.thumbnail:
                                            size = 150
                                            frame = Frame().set_margin(32).set_content_align('c').set_size((size, size))
                                            with frame:
                                                # 创建副本以避免修改原始缩略图对象，或者直接在ImageBox处理
                                                thumb_copy = item.thumbnail.copy()
                                                thumb_copy.thumbnail((size, size))
                                                ImageBox(thumb_copy)
                                        with VSplit().set_sep(12).set_item_align('l').set_content_align('l'):
                                            with HSplit().set_sep(6).set_item_align('l').set_content_align('l'):
                                                TextBox(f"#{i + 1}", style=TextStyle(font=DEFAULT_BOLD_FONT, size=32, color=text_color1))
                                                Spacer(w=8)
                                                if item.source:
                                                    TextBox(f"From {item.source}", style=TextStyle(font=DEFAULT_FONT, size=24, color=text_color2))
                                                if item.source_icon:
                                                    ImageBox(item.source_icon, size=(None, 24)).set_offset((0, 4))
                                                if item.similarity:
                                                    TextBox(f"相似度: {item.similarity:.2f}%", style=TextStyle(font=DEFAULT_FONT, size=24, color=text_color2))
                                            if item.title:
                                                TextBox(item.title, style=TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=text_color1)).set_w(w)
                                            if item.url:
                                                # url 可能会很长，TextBox 会自动换行
                                                TextBox(item.url, style=TextStyle(font=DEFAULT_FONT, size=24, color=text_color2)).set_w(w)

    return await canvas.get_img(), results
