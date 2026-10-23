"""群友命令：随机发图、投稿、查看stickers、看所有、查看编号"""

import asyncio
import re
from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    GROUP,
    Bot,
    GroupMessageEvent,
    Message,
)
from nonebot.params import CommandArg, CommandWhitespace
from nonebot.typing import T_State

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from core.settings import ConfigModel, load_config
from plugins.stickers import render, store
from utils.images.dedupe import find_duplicate
from utils.images.formats import MIME_TYPES
from utils.logging import get_exc_desc
from utils.network.images import DownloadedImage, fetch_image
from utils.onebot.media import image_segment
from utils.onebot.messages import find_forward_id, normalize_message_segments
from utils.onebot.rules import command_boundary


class LocalConfig(ConfigModel):
    # 表情包下载站的网址，「查看stickers」发预览图时附在后面；没配就不附
    site_url: str


SITE_URL = load_config("stickers", LocalConfig).site_url

# 随机发图、查看编号一次最多几张
MAX_COUNT = 5
DOWNLOAD_CONCURRENCY = 5

RANDOM_PATTERN = re.compile(r"^随机\s*(\S+?)\s*(?:(?:乘以|[x×*乘])\s*(\d+))?$", re.I)
CONTRIBUTE_PATTERN = re.compile(r"^(.+?)投稿(\s+force)?$", re.I)


# --- 随机 ---


async def _random_rule(event: GroupMessageEvent, state: T_State) -> bool:
    match = RANDOM_PATTERN.match(event.get_plaintext().strip())
    if not match:
        return False
    name, count = match.groups()
    if name.lower() in store.ALL_KEYWORDS:
        folder = None
    else:
        folder = store.resolve(name)
        if folder is None:
            return False
    state["random"] = (folder, max(1, min(int(count or 1), MAX_COUNT)))
    return True


random_matcher = on_message(
    rule=_random_rule & switch_rule("stickers"), priority=10, block=False
)


@random_matcher.handle()
async def handle_random(event: GroupMessageEvent, state: T_State):
    # 冷却中静默
    if cooldown_remaining("stickers", event) > 0:
        return
    files = store.random_images(*state["random"])
    if not files:
        return
    mark_cooldown("stickers", event)
    await random_matcher.finish(Message(image_segment(path) for path in files))


# --- 投稿 ---


async def _contribute_rule(event: GroupMessageEvent, state: T_State) -> bool:
    # 文件夹对不上就不算投稿：「怎么删投稿」这类日常说法不该触发
    match = CONTRIBUTE_PATTERN.match(event.get_plaintext().strip())
    if not match:
        return False
    name = match[1].strip()
    folder = store.resolve(name)
    if folder is None:
        return False
    state["contribute"] = (name, folder, bool(match[2]))
    return True


contribute_matcher = on_message(
    rule=_contribute_rule & switch_rule("stickers"), priority=10, block=False
)


async def _image_urls(bot: Bot, event: GroupMessageEvent) -> list[str]:
    """被回复的合并转发 > 被回复的消息 > 本条消息，取第一个有图的来源"""
    segments = []
    if event.reply:
        forward_id = find_forward_id(event.reply.message)
        if forward_id:
            try:
                forward = await bot.get_forward_msg(id=forward_id)
            except Exception as e:
                logger.warning(f"获取合并转发消息失败: {get_exc_desc(e)}")
                await contribute_matcher.finish(
                    "投稿失败！解析合并转发内容失败，请稍后再试。"
                )
            for node in forward.get("messages", []):
                segments += normalize_message_segments(node.get("message", ""))
        else:
            segments = normalize_message_segments(event.reply.message)
    urls = [s["data"].get("url") for s in segments if s["type"] == "image"]
    if not any(urls):
        urls = [s.data.get("url") for s in event.message if s.type == "image"]
    return list(dict.fromkeys(url for url in urls if url))


async def _download(urls: list[str]) -> list[DownloadedImage]:
    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

    async def download(url: str):
        async with semaphore:
            try:
                return await fetch_image(
                    url, timeout=30, allowed_formats=tuple(MIME_TYPES)
                )
            except Exception as e:
                logger.warning(f"下载投稿图片失败: {url}: {get_exc_desc(e)}")
                return None

    results = await asyncio.gather(*(download(url) for url in urls))
    return [image for image in results if image]


@contribute_matcher.handle()
async def handle_contribute(bot: Bot, event: GroupMessageEvent, state: T_State):
    name, folder, force = state["contribute"]
    urls = await _image_urls(bot, event)
    if not urls:
        await contribute_matcher.finish(
            "投稿失败！未检测到图片，请直接发送图片、回复图片或回复合并转发消息"
        )
    downloaded = await _download(urls)
    if not downloaded:
        await contribute_matcher.finish("投稿失败！无法下载任何图片")

    existing = store.images(folder)
    duplicates = []
    for image in downloaded:
        duplicate = (
            None
            if force
            else await asyncio.to_thread(find_duplicate, image.data, existing)
        )
        if duplicate:
            duplicates.append((duplicate, image))
        else:
            store.add_image(folder, image.data, image.extension)
    saved = len(downloaded) - len(duplicates)
    logger.info(
        f"群 {event.group_id} 向 {folder} 投稿：保存 {saved} 张，重复 {len(duplicates)} 张"
    )

    if saved:
        display = folder if name == folder else f"{folder}(通过别名'{name}')"
        text = f"投稿完成！成功保存 {saved} 张图片。"
        if duplicates:
            text += f"\n检测到 {len(duplicates)} 张重复图片。"
        text += f"\n现在 {display} 中共有 {len(store.images(folder))} 张表情~"
    else:
        text = f"投稿失败！{len(duplicates)} 张图片全部为重复图片。"
    message = Message(text)
    if duplicates:
        report = await render.contribution_duplicates(name, duplicates)
        if report:
            message += image_segment(report)
    await contribute_matcher.finish(message)


# --- 查看stickers ---

overview_matcher = on_command(
    "查看stickers",
    rule=command_boundary & switch_rule("stickers"),
    permission=GROUP,
    priority=5,
    block=True,
)


@overview_matcher.handle()
async def handle_overview():
    async def send_preview():
        image = await render.folders_preview()
        await overview_matcher.send(image_segment(image) if image else "预览图生成失败")

    async def send_link():
        if SITE_URL:
            await overview_matcher.send(f"下载链接：{SITE_URL}")

    # 预览图渲染和上传都慢，链接不用等它
    await asyncio.gather(send_preview(), send_link())


# --- 看所有 / 查看编号 ---


# 帮助里的写法是「看所有miku」「查看947」，参数可以连写，所以改为检查参数本身：
# 看所有 连写时必须是已有文件夹，查看 第一个参数必须是编号。
# 否则「看所有人」「查看收藏」「no way」这类日常说法也会触发
async def _view_all_rule(
    arg: Message = CommandArg(), whitespace: str | None = CommandWhitespace()
) -> bool:
    if await command_boundary(arg, whitespace):
        return True
    return store.resolve(arg.extract_plain_text().strip()) is not None


async def _view_single_rule(arg: Message = CommandArg()) -> bool:
    tokens = arg.extract_plain_text().split()
    return bool(tokens) and tokens[0].isdigit()


view_all_matcher = on_command(
    "看所有",
    aliases={"查看所有", "view all"},
    rule=_view_all_rule & switch_rule("stickers"),
    permission=GROUP,
    priority=5,
    block=True,
)
view_single_matcher = on_command(
    "sticker",
    aliases={"看表情", "No.", "NO", "查看", "no", "no."},
    rule=_view_single_rule & switch_rule("stickers"),
    permission=GROUP,
    priority=5,
    block=True,
)


@view_all_matcher.handle()
async def handle_view_all(args: Message = CommandArg()):
    name = args.extract_plain_text().strip()
    if not name:
        await view_all_matcher.finish("请指定要查看的文件夹名称，例如：看所有猫猫")
    folder = store.resolve(name)
    if folder is None:
        await view_all_matcher.finish(f"未找到文件夹 '{name}'")
    files = store.images(folder)
    if not files:
        await view_all_matcher.finish(f"文件夹 '{folder}' 中没有图片")

    size = render.OVERVIEW_PAGE_SIZE
    pages = [files[i : i + size] for i in range(0, len(files), size)]
    if len(pages) > 1:
        await view_all_matcher.send(
            f"文件夹 '{folder}' 共 {len(files)} 张图片，将分为 {len(pages)} 张概览图发送，请稍候..."
        )
    else:
        await view_all_matcher.send(
            f"正在生成 '{folder}' 的概览，共 {len(files)} 张图片，请稍候..."
        )

    for number, page in enumerate(pages, 1):
        try:
            image = await asyncio.to_thread(
                render.gallery_overview, page, folder, number, len(pages)
            )
        except Exception:
            logger.exception(f"生成 {folder} 第 {number} 页概览图失败")
            await view_all_matcher.finish(f"第 {number} 页概览图生成失败")
        message = Message(image_segment(image))
        if len(pages) > 1:
            message += f"\nPart {number}/{len(pages)} ({len(page)} items)"
        await view_all_matcher.send(message)


@view_single_matcher.handle()
async def handle_view_single(args: Message = CommandArg()):
    tokens = args.extract_plain_text().split()
    if len(tokens) > MAX_COUNT:
        await view_single_matcher.finish(f"一次最多只能查看 {MAX_COUNT} 张图片哦。")

    message, missing = Message(), []
    for token in tokens:
        if not token.isdigit():
            continue
        path = store.find_by_id(int(token))
        if path:
            message += image_segment(path)
        else:
            missing.append(token)
    if missing:
        note = f"找不到编号 {' '.join(missing)}"
        message += f"\n⚠️ {note}" if message else note
    await view_single_matcher.finish(message)
