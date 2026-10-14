import re
import asyncio
import argparse
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Tuple
from contextlib import ExitStack

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot
from nonebot.params import CommandArg
from nonebot.matcher import Matcher

from PIL import Image

from plugins.lunabot_imgexp.config import config
from utils.logging import get_exc_desc
from utils.concurrency import run_in_pool
from utils.text import truncate
from utils.files import TemporaryPath
from utils.onebot.forward import send_forward_msg
from utils.network.http import (
    DownloadTooLarge,
    download_bytes,
    download_image,
    download_to_file,
    get_client_session,
    get_effective_proxy,
)
from plugins.lunabot_imgexp.image_utils import (
    concat_images,
    save_transparent_static_gif,
)
from utils.onebot.media import image_segment, outbound_media_path, video_segment

from core.access import is_feature_enabled
from core.cooldown import check_cd, update_cd

PLUGIN_NAME = "lunabot_imgexp"

# ==================== 推特图片/视频下载 ==================== #


class ReplyException(Exception):
    pass


# X 网页版未登录时对无头浏览器只返回空白页，改走 fxtwitter 的公开 JSON 接口
FXTWITTER_API = "https://api.fxtwitter.com/status/{}"
# 超过此大小的视频降级到更低码率，仍超出则放弃
VIDEO_MAX_BYTES = 100 * 1024 * 1024


@dataclass
class Tweet:
    content: str
    photo_urls: List[str]
    # (媒体 id, mp4 直链)
    videos: List[Tuple[str, str]]


def pick_video_url(media: dict) -> str:
    """选不超过大小上限的最高码率 mp4，都超出时取最低码率。"""
    mp4s = sorted(
        (f for f in media.get("formats", []) if f.get("container") == "mp4"),
        key=lambda f: f.get("bitrate", 0),
    )
    if not mp4s:
        return media["url"]
    duration = media.get("duration") or 0
    fitting = [
        f for f in mp4s if f.get("bitrate", 0) * duration / 8 <= VIDEO_MAX_BYTES
    ]
    return (fitting[-1] if fitting else mp4s[0])["url"]


async def get_x_content(url: str) -> Tweet:
    match = re.search(r"/status(?:es)?/(\d+)", url)
    if not match:
        raise ReplyException(
            "链接格式不正确，需要形如 https://x.com/xxx/status/12345 的推文链接"
        )

    async with get_client_session().get(
        FXTWITTER_API.format(match.group(1)),
        proxy=get_effective_proxy(config.get("proxy")),
    ) as resp:
        data = await resp.json(content_type=None)
    if data.get("code") != 200:
        raise ReplyException(
            f"获取推文失败（{data.get('code')} {data.get('message')}），"
            "可能已被删除或为私密账号"
        )

    tweet = data["tweet"]
    photo_urls, videos = [], []
    for media in (tweet.get("media") or {}).get("all", []):
        if media["type"] == "photo":
            photo_urls.append(media["url"])
        else:
            # video 与 gif 都是 mp4
            videos.append((media["id"], pick_video_url(media)))

    content = f"{tweet['author']['name']}: {tweet.get('text', '').strip()}"
    return Tweet(content, photo_urls, videos)


# 简单的参数解析器
class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReplyException(message)


ximg = on_command("ximg", aliases={"x img", "tw img", "推图"}, priority=5, block=True)


@ximg.handle()
async def _(
    bot: Bot, matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 检查功能开关
    if not is_feature_enabled(PLUGIN_NAME, "ximg", group_id, user_id):
        await ximg.finish()

    cd_key = f"{PLUGIN_NAME}:ximg"
    cd_remain = check_cd(cd_key, group_id, user_id)
    if cd_remain > 0:
        await ximg.finish(f"功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

    raw_args = args.extract_plain_text().strip().split()

    parser = ArgumentParser(description="推特图片下载", add_help=False)
    parser.add_argument("url", type=str, nargs="?", help="X推文链接")
    parser.add_argument("--vertical", "-V", action="store_true")
    parser.add_argument("--horizontal", "-H", action="store_true")
    parser.add_argument("--grid", "-G", action="store_true")
    parser.add_argument("--gif", "-g", action="store_true")

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
使用方式: /ximg <url> [-V] [-H] [-G] [-g]
-V: 垂直拼图 -H: 水平拼图 -G 网格拼图 -g 转换为GIF
不加参数默认各个图片分开发送，推文中的视频会单独发送
示例: /ximg https://x.com/xxx/status/12345 -G
""".strip()
        )

    if [args_obj.vertical, args_obj.horizontal, args_obj.grid].count(True) > 1:
        await ximg.finish("只能选择一种拼图模式")

    concat_mode = (
        "v"
        if args_obj.vertical
        else "h"
        if args_obj.horizontal
        else "g"
        if args_obj.grid
        else None
    )

    await ximg.send("正在获取推文内容...")

    try:
        logger.debug(f"获取X推文: {url}")
        tweet = await get_x_content(url)
        image_urls = tweet.photo_urls[:16]
        logger.debug(f"获取到图片: {image_urls} 视频: {tweet.videos}")
    except ReplyException as e:
        await ximg.finish(str(e))
    except Exception as e:
        logger.error(f"获取X推文失败: {get_exc_desc(e)}")
        await ximg.finish(f"获取推文失败: {get_exc_desc(e)}")

    if not image_urls and not tweet.videos:
        await ximg.finish("这条推文里没有图片或视频")

    msg_content = url + "\n" + truncate(tweet.content, 64)
    messages = [MessageSegment.text(msg_content)]

    images = []
    if image_urls:
        try:
            if concat_mode or args_obj.gif:
                images = await asyncio.gather(
                    *[download_image(u, proxy=config.get("proxy")) for u in image_urls]
                )
            else:
                # 不拼图时直接发原图字节，免得把原图 JPEG 转成体积更大的 PNG
                images = await asyncio.gather(
                    *[download_bytes(u, proxy=config.get("proxy")) for u in image_urls]
                )
        except Exception as e:
            logger.warning(f"下载推文图片失败: {get_exc_desc(e)}")
            await ximg.finish(f"下载图片失败: {get_exc_desc(e)}")

    if concat_mode and images:
        try:
            images = [await run_in_pool(concat_images, images, concat_mode)]
        except Exception as e:
            logger.exception("拼图失败")
            await ximg.finish(f"拼图失败: {e}")

    # 使用 ExitStack 管理多个临时文件
    with ExitStack() as stack:
        for img in images:
            if args_obj.gif:
                gif_path_ctx = TemporaryPath("gif", remove_after=timedelta(minutes=3))
                gif_path = stack.enter_context(gif_path_ctx)
                await run_in_pool(save_transparent_static_gif, img, str(gif_path))
                messages.append(image_segment(gif_path))

            elif isinstance(img, Image.Image):
                tmp_path = stack.enter_context(TemporaryPath("png"))
                img.save(tmp_path, format="PNG")
                messages.append(image_segment(tmp_path))

            else:
                messages.append(image_segment(img))

        if images:
            await send_forward_msg(bot, event, items=messages)
        else:
            await ximg.send(msg_content)

    # 合并转发里的视频节点不稳定，视频逐条单独发
    for media_id, video_url in tweet.videos:
        path = outbound_media_path(f"x_{media_id}.mp4")
        try:
            if not path.exists():
                await download_to_file(
                    video_url,
                    path,
                    proxy=config.get("proxy"),
                    max_bytes=VIDEO_MAX_BYTES,
                    timeout=300,
                )
        except DownloadTooLarge:
            await ximg.send(
                f"视频超过 {VIDEO_MAX_BYTES // 1024 // 1024}MB，未发送: {video_url}"
            )
            continue
        except Exception as e:
            logger.warning(f"下载推文视频失败 {video_url}: {get_exc_desc(e)}")
            await ximg.send(f"下载视频失败: {get_exc_desc(e)}")
            continue
        await ximg.send(video_segment(path))

    # 发送成功后再更新 CD
    update_cd(cd_key, group_id, user_id)
