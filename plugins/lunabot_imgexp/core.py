import asyncio
from typing import List, Tuple, Optional, Any
from dataclasses import dataclass
from urllib.parse import quote
from PIL import Image
from PicImageSearch import SauceNAO, Network, Ascii2D, TraceMoe, Iqdb

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
    client: Any,
    img_url: str, 
    limit: int = 10,
    min_similarity: int = 60
) -> ImageSearchResult:
    try:
        if limit == 0: 
            return ImageSearchResult(source='SauceNAO', results=[])
        logger.info("开始从SauceNAO搜索图片...")
        
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
        # import traceback
        # traceback.print_exc()
        return ImageSearchResult(source='SauceNAO', results=[], error=f"搜索失败: {e}")


async def search_ascii2d(
    client: Any,
    img_url: str,
    limit: int = 2
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source='Ascii2D', results=[])
        logger.info("开始从Ascii2D搜索图片...")
        
        ascii2d = Ascii2D(client=client)
        # Ascii2D usually does color search first.
        results = await ascii2d.search(url=img_url)
        
        # Ascii2D results raw list
        filtered_results = results.raw[:limit]
        
        thumbnails = await download_batch_thumbnails([item.thumbnail for item in filtered_results])
        
        res_items = []
        for item, thumbnail in zip(filtered_results, thumbnails):
            # Ascii2D items usually have url, title, thumbnail, detail
            # detail often contains author info
            title = item.title or "Unknown"
            if hasattr(item, 'detail') and item.detail:
                title += f" ({item.detail})"
                
            res_items.append(ImageSearchResultItem(
                title=title,
                url=item.url,
                similarity=None, # Ascii2D usually doesn't provide similarity percentage
                thumbnail=thumbnail,
                source="Ascii2D"
            ))
            
        logger.info(f"从Ascii2D搜索到 {len(res_items)} 个结果")
        return ImageSearchResult(source='Ascii2D', results=res_items)

    except Exception as e:
        logger.warning(f'从Ascii2D搜索图片 {img_url} 失败: {e}')
        return ImageSearchResult(source='Ascii2D', results=[], error=f"搜索失败: {e}")


async def search_tracemoe(
    client: Any,
    img_url: str,
    limit: int = 5
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source='TraceMoe', results=[])
        logger.info("开始从TraceMoe搜索图片...")
        
        # TraceMoe supports API key but it's optional
        # Wait, PicImageSearch TraceMoe init signature might not take api_key directly in init?
        # Checked earlier: TraceMoe.search takes key
        tracemoe = TraceMoe(client=client)
        # Assuming TraceMoe.search(url=..., key=...)
        # Note: PicImageSearch TraceMoe.search signature: (url=None, file=None, key=None, ...)
        
        api_key = config.get('tracemoe_apikey')
        results = await tracemoe.search(url=img_url, key=api_key)
        
        filtered_results = results.raw[:limit]
        
        thumbnails = await download_batch_thumbnails([item.thumbnail for item in filtered_results])
        
        res_items = []
        for item, thumbnail in zip(filtered_results, thumbnails):
            # TraceMoe items: anime, episode, similarity (0-1), from_time, to_time, video, image, thumbnail
            title = item.anime or "Unknown Anime"
            if hasattr(item, 'episode') and item.episode:
                title += f" Ep {item.episode}"
            
            # format time
            if hasattr(item, 'from_time'):
                m, s = divmod(int(item.from_time), 60)
                title += f" ({m:02d}:{s:02d})"

            similarity = item.similarity * 100 if item.similarity else 0
            
            res_items.append(ImageSearchResultItem(
                title=title,
                url=item.video if hasattr(item, 'video') else item.thumbnail, # Use video preview as URL if available
                similarity=similarity,
                thumbnail=thumbnail,
                source="TraceMoe"
            ))
            
        logger.info(f"从TraceMoe搜索到 {len(res_items)} 个结果")
        return ImageSearchResult(source='TraceMoe', results=res_items)

    except Exception as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "rate limit" in error_msg:
            logger.info(f'从TraceMoe搜索图片 {img_url} 失败: 配额耗尽 (Rate Limit)，已跳过')
            return ImageSearchResult(source='TraceMoe', results=[])
        
        logger.warning(f'从TraceMoe搜索图片 {img_url} 失败: {e}')
        return ImageSearchResult(source='TraceMoe', results=[], error=f"搜索失败: {e}")


async def search_iqdb(
    client: Any,
    img_url: str,
    limit: int = 5
) -> ImageSearchResult:
    try:
        if limit == 0:
            return ImageSearchResult(source='IQDB', results=[])
        logger.info("开始从IQDB搜索图片...")
        
        iqdb = Iqdb(client=client)
        results = await iqdb.search(url=img_url)
        
        filtered_results = results.raw[:limit]
        
        thumbnails = await download_batch_thumbnails([item.thumbnail for item in filtered_results])
        
        res_items = []
        for item, thumbnail in zip(filtered_results, thumbnails):
            # IQDB items: url, title? (source?), similarity, thumbnail
            # IQDB often gives Danbooru/Konachan links
            title = "IQDB Result"
            # Try to get more info if possible, but basic is fine
            
            similarity = item.similarity if item.similarity else None
            
            res_items.append(ImageSearchResultItem(
                title=title,
                url=item.url,
                similarity=similarity,
                thumbnail=thumbnail,
                source="IQDB"
            ))
            
        logger.info(f"从IQDB搜索到 {len(res_items)} 个结果")
        return ImageSearchResult(source='IQDB', results=res_items)

    except Exception as e:
        logger.warning(f'从IQDB搜索图片 {img_url} 失败: {e}')
        return ImageSearchResult(source='IQDB', results=[], error=f"搜索失败: {e}")


async def search_googlelens(
    img_url: str,
    limit: int = 10,
) -> ImageSearchResult:
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
        # import traceback
        # traceback.print_exc()
        return ImageSearchResult(source='GoogleLens', results=[], error=f"搜索失败: {e}")


async def search_image(
    img_url: str,
    img_size: int = 0, # img_size 默认为0，如果不提供
) -> Tuple[Image.Image, List[ImageSearchResult]]:
    SIZE_LIMIT_MB = 15
    if img_size > SIZE_LIMIT_MB * 1024 * 1024:
        raise ValueError(f"图片大小超过{SIZE_LIMIT_MB}MB，请先缩小后再进行搜索")

    proxy = config.get('proxy')
    proxies = f"http://{proxy}" if proxy else None

    async with Network(timeout=30, proxies=proxies) as client:
        # 并行执行所有搜索
        # SauceNAO
        task_saucenao = search_saucenao(client, img_url)
        # Ascii2D
        task_ascii2d = search_ascii2d(client, img_url)
        # TraceMoe
        task_tracemoe = search_tracemoe(client, img_url)
        # IQDB
        task_iqdb = search_iqdb(client, img_url)
        # GoogleLens (independent client)
        task_google = search_googlelens(img_url)

        results = await asyncio.gather(
            task_saucenao, 
            task_ascii2d, 
            task_tracemoe, 
            task_iqdb,
            task_google
        )
    
    # results is a list of ImageSearchResult
    # Filter out empty results if desired, or keep them to show "Not Found"
    # The painter logic handles errors/empty results, so we keep them.
    
    # Drawing Logic
    bg = FillBg(LinearGradient(c1=(220, 220, 255, 255), c2=(220, 240, 255, 255), p1=(0, 0), p2=(1, 1)))
    item_bg = RoundRectBg((255, 255, 255, 125), 10, blurglass=True)
    text_color1 = (50, 50, 50, 255)
    text_color2 = (75, 75, 75, 255)
    w = 800
    
    canvas = Canvas(bg=bg).set_padding(10)
    with canvas:
        with VSplit().set_sep(16).set_padding(16).set_item_bg(item_bg).set_content_align('l').set_item_align('l'):
            for result in results:
                # 只显示有结果的或者是错误的（如果用户想看错误信息）
                # 这里我们稍微优化一下：如果结果为空且没有错误，可以跳过不显示，或者显示"未找到"
                # 原逻辑是显示 "未找到结果"
                
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
                                                if item.similarity is not None:
                                                    TextBox(f"相似度: {item.similarity:.2f}%", style=TextStyle(font=DEFAULT_FONT, size=24, color=text_color2))
                                            if item.title:
                                                TextBox(item.title, style=TextStyle(font=DEFAULT_BOLD_FONT, size=28, color=text_color1)).set_w(w)
                                            if item.url:
                                                TextBox(item.url, style=TextStyle(font=DEFAULT_FONT, size=24, color=text_color2)).set_w(w)

    img = await canvas.get_img()

    # 绘制水印
    watermark_text = config.get('watermark_text', '')
    if watermark_text:
        p = Painter(img)
        font_size = 20
        font = get_font(DEFAULT_BOLD_FONT, font_size)
        lines = watermark_text.strip().split('\n')
        line_height = font_size + 4
        padding = 15
        
        # 计算起始 Y 坐标 (图片高度 - 总高度 - 底部边距)
        total_height = len(lines) * line_height
        start_y = img.height - total_height - padding
        
        cur_y = start_y
        for line in lines:
            if not line:
                cur_y += line_height
                continue
            
            w, h = get_text_size(font, line)
            x = img.width - w - padding
            
            # 绘制阴影 (黑色半透明)
            p.text(line, (x + 2, cur_y + 2), font, fill=(0, 0, 0, 128))
            # 绘制主体 (白色半透明)
            p.text(line, (x, cur_y), font, fill=(255, 255, 255, 180))
            
            cur_y += line_height
        
        img = await p.get()

    return img, results
