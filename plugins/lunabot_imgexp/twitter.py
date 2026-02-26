import re
import time
import asyncio
import argparse
from datetime import timedelta
from typing import List, Tuple, Union
from contextlib import ExitStack

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.typing import T_State
from nonebot.rule import to_me

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from PIL import Image

from .config import config
from ..utils.tools import get_logger, get_exc_desc, run_in_pool, truncate, TempFilePath, send_forward_msg
from ..utils.network import download_image
from ..utils.browser import PlaywrightPage
from ..utils.draw.img_utils import concat_images, save_transparent_static_gif
from ..utils.image_utils import path_to_base64_image

logger = get_logger('Twitter')

try:
    from ..plugin_manager.enable import is_plugin_enabled, is_feature_enabled
    from ..plugin_manager.cd_manager import check_cd, update_cd
    MANAGER_AVAILABLE = True
except ImportError:
    MANAGER_AVAILABLE = False

PLUGIN_NAME = "lunabot_imgexp"

# ==================== 推特图片下载 ==================== #

class ReplyException(Exception):
    pass

async def get_x_content(url: str) -> Tuple[str, List[str]]:
    """从 X 帖子 URL 中提取文本内容和图片链接。"""
    image_urls = []

    async def block_agressive_resources(route):
        """拦截图片以外的非必要资源"""
        if route.request.resource_type in ["font", "stylesheet", "media", "websocket"]:
            await route.abort()
        elif "google-analytics" in route.request.url or "monitor" in route.request.url:
            await route.abort()
        else:
            await route.continue_()
    
    async with PlaywrightPage() as page:
        await page.route("**/*", block_agressive_resources)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # 等待推文核心内容出现
            tweet_selector = 'article[data-testid="tweet"]'
            try:
                await page.wait_for_selector(tweet_selector, state="visible", timeout=10000)
            except PlaywrightTimeoutError:
                raise ReplyException(f"未能找到推文内容，可能是由登录墙、已被删除或网络超时引起，请稍后再试")

            # 处理“敏感内容”或“显示更多”按钮
            sensitive_overlay_selector = '[data-testid="tweet"] div[role="button"]:has-text("View"), [data-testid="tweet"] div[role="button"]:has-text("Show")'
            if await page.locator(sensitive_overlay_selector).count() > 0:
                try:
                    # 点击所有覆盖层
                    overlays = await page.locator(sensitive_overlay_selector).all()
                    for overlay in overlays:
                        if await overlay.is_visible():
                            await overlay.click(force=True)
                            await page.wait_for_timeout(500) # 给一点渲染时间
                except Exception as e:
                    raise ReplyException(f"尝试点击敏感内容遮罩时出错: {get_exc_desc(e)}")

            # 提取图片
            photo_selector = 'div[data-testid="tweetPhoto"] img'
            try:
                await page.wait_for_selector(photo_selector, state="attached", timeout=3000)
            except PlaywrightTimeoutError:
                pass

            img_locators = await page.locator(photo_selector).all()
            
            for locator in img_locators:
                src = await locator.get_attribute("src")
                if src:
                    # URL 清洗/优化
                    clean_src = src
                    if "pbs.twimg.com/media" in src:
                        if "name=" in src:
                            clean_src = re.sub(r'name=[a-z0-9]+', 'name=large', src)
                        else:
                            clean_src = src + "&name=large"
                            
                    if clean_src not in image_urls:
                        image_urls.append(clean_src)

            # 提取用户名
            user_locator = page.locator(f'{tweet_selector} [data-testid="User-Name"]')
            username_text = await user_locator.inner_text()
            display_name = username_text.split('\n')[0] if username_text else "Unknown"

            # 提取推文正文
            text_locator = page.locator(f'{tweet_selector} [data-testid="tweetText"]')
            content = ""
            if await text_locator.count() > 0:
                content = await text_locator.inner_text()

            full_content = f"{display_name}: {content.strip()}"

        except Exception as e:
            # 保存调试用页面截图
            # screenshot_path = f"data/imgexp/debug/x_{int(time.time())}.png"
            # await page.screenshot(path=screenshot_path, full_page=True)
            if isinstance(e, ReplyException):
                raise e
            raise e

    return full_content, image_urls


# 简单的参数解析器
class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReplyException(message)

ximg = on_command("ximg", aliases={"x img", "tw img", "推图"}, priority=5, block=True)

@ximg.handle()
async def _(bot: Bot, matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()):
    # 插件管理检查
    if MANAGER_AVAILABLE:
        group_id = str(event.group_id)
        user_id = str(event.user_id)
        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await ximg.finish()
        
        # 检查功能开关
        if not is_feature_enabled(PLUGIN_NAME, "ximg", group_id, user_id):
            await ximg.finish()

        cd_key = f"{PLUGIN_NAME}:ximg"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await ximg.finish(f"功能冷却中，请等待 {cd_remain} 秒", at_sender=True)
        update_cd(cd_key, group_id, user_id)

    raw_args = args.extract_plain_text().strip().split()
    
    parser = ArgumentParser(description="推特图片下载", add_help=False)
    parser.add_argument('url', type=str, nargs='?', help='X推文链接')
    parser.add_argument('--vertical',   '-V', action='store_true')
    parser.add_argument('--horizontal', '-H', action='store_true')
    parser.add_argument('--grid',       '-G', action='store_true')
    parser.add_argument('--fold',       '-f', action='store_true')
    parser.add_argument('--gif',        '-g', action='store_true')

    try:
        args_obj = parser.parse_args(raw_args)
    except ReplyException as e:
        await ximg.finish(str(e))
    except Exception as e:
        await ximg.finish(f"参数解析错误: {e}")

    url = args_obj.url
    if not url:
        await ximg.finish(
"""
使用方式: /ximg <url> [-V] [-H] [-G] [-f]
-V: 垂直拼图 -H: 水平拼图 -G 网格拼图 
-f 折叠回复 -g 转换为GIF
不加参数默认各个图片分开发送
示例: /ximg https://x.com/xxx/status/12345 -G
""".strip())

    if [args_obj.vertical, args_obj.horizontal, args_obj.grid].count(True) > 1:
        await ximg.finish('只能选择一种拼图模式')
    
    concat_mode = 'v' if args_obj.vertical else 'h' if args_obj.horizontal else 'g' if args_obj.grid else None

    await ximg.send("正在获取推文内容...")

    try:
        logger.info(f'获取X图片链接: {url}')
        content, image_urls = await get_x_content(url)
        image_urls = image_urls[:16]
        logger.info(f'获取到图片链接: {image_urls}')
    except ReplyException as e:
        await ximg.finish(str(e))
    except Exception as e:
        logger.error(f'获取X图片链接失败: {get_exc_desc(e)}')
        await ximg.finish(f'获取图片链接失败: {get_exc_desc(e)}')
    
    if not image_urls:
        await ximg.finish('在推文中没有找到图片，可能是输入网页链接不正确或其他原因')
    
    msg_content = url + "\n" + truncate(content, 64)
    messages = [MessageSegment.text(msg_content)]

    force_download_image = True     # 不下载到本地再发送可能导致napcat报错

    if force_download_image or concat_mode or args_obj.gif:
        try:
            images = await asyncio.gather(
                *[download_image(u, proxy=config.get("proxy")) for u in image_urls]
            )
        except Exception as e:
            await ximg.finish(f"下载图片失败: {e}")
            return
    else:
        images = image_urls

    if concat_mode:
        try:
            concated_image = await run_in_pool(concat_images, images, concat_mode)
            images = [concated_image]
        except Exception as e:
            await ximg.finish(f"拼图失败: {e}")
            return

    # 使用 ExitStack 管理多个临时文件
    with ExitStack() as stack:
        for img in images:
            if args_obj.gif:
                # GIF 处理
                gif_path_ctx = TempFilePath("gif", remove_after=timedelta(minutes=3))
                gif_path = stack.enter_context(gif_path_ctx)
                await run_in_pool(save_transparent_static_gif, img, str(gif_path))
                messages.append(path_to_base64_image(gif_path))
            
            elif isinstance(img, Image.Image):
                # PIL Image -> Temp File
                tmp_ctx = TempFilePath("png")
                tmp_path = stack.enter_context(tmp_ctx)
                img.save(tmp_path, format='PNG')
                messages.append(path_to_base64_image(tmp_path))
            
            elif isinstance(img, bytes):
                # Bytes -> Temp File
                # 简单起见假设是 jpg/png，OneBot 通常能自动识别
                tmp_ctx = TempFilePath("jpg") 
                tmp_path = stack.enter_context(tmp_ctx)
                with open(tmp_path, 'wb') as f:
                    f.write(img)
                messages.append(path_to_base64_image(tmp_path))
                
            else:
                # URL string
                messages.append(MessageSegment.image(img))

        # 发送逻辑
        # 使用合并转发发送
        await send_forward_msg(bot, event, messages)
