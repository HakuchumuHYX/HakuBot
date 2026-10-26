"""bili_dyn_sub 推送渲染：文字模板 + 文转图 + 九宫格拼图，产出有序的消息段。

逐字符复刻 nonebot-bison (MIT, Copyright (c) 2021 felinae98) 默认配置下的输出
（basic 主题、bison_use_pic=True、bison_use_pic_merge=1）：
- theme/themes/basic/build.py：文字模板与配图分组顺序；
- utils/image.py：九宫格判据与拼接坐标；
- 文字卡片走 utils/rendering/engine.py 的 render_text，模板与 bison 的 htmlrender 一致。
发送节奏（首条单发、余图合并转发）在 scheduler.py。
"""

from __future__ import annotations

import asyncio
from io import BytesIO

import aiohttp
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot import logger
from PIL import Image
from PIL.Image import Image as PILImage

from utils.rendering.engine import render_text
from utils.onebot.media import image_segment
from utils.network.http import HttpError, download_bytes
from utils.logging import get_exc_desc
from plugins.bili_dyn_sub.config import plugin_config
from plugins.bili_dyn_sub.parser import DELETED_SOURCE_TIPS, ParsedDynamic

# ---------------------------------------------------------------- 常量

PLATFORM_NAME = "B站"  # "来源: B站 {昵称}"
SEPARATOR = "--------------"  # 14 个 "-"
# 配图下载 / 解码失败只放弃合并，不影响推送
_PIC_ERRORS = (
    HttpError,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    OSError,
    ValueError,
)


# ---------------------------------------------------------------- 文字模板


def _truncate(content: str) -> str:
    """照抄 bison：`content if len(content) < 500 else f"{content[:500]}..."`（注意是严格小于）"""
    limit = plugin_config.text_truncate_length
    return content if len(content) < limit else f"{content[:limit]}..."


def _detail_url(parsed: ParsedDynamic) -> str:
    """详情链接。

    bison 的 `post.url` 取自各 major 的 `jump_url`（视频→BV 链接、专栏→cv 链接…），
    只有 DRAW / 未知 major / 无 major 才回落到 `https://t.bilibili.com/{id}`；
    parser 已把两者分别存进 `major_url` / `url`，此处按 bison 的优先级取，保证逐字符一致。
    源动态已被删除时两者都是空串 → 调用方不输出该行（bison 那里是 `url=None`）。
    """
    return parsed.major_url or parsed.url


def build_text(parsed: ParsedDynamic) -> str:
    """构造推送文本（复刻 bison basic 主题）"""
    text = ""

    text += f"{parsed.title}\n\n" if parsed.title else ""
    text += _truncate(parsed.content)

    rp = parsed.repost
    if rp:
        text += f"\n{SEPARATOR}\n转发自 {rp.nickname or ''}:\n"
        text += f"{rp.title}\n\n" if rp.title else ""
        rp_content = rp.content
        if rp.is_deleted_source and not rp_content:
            # 源动态已被删除且 B 站没给 tips：用降级文案，别推一段空白
            rp_content = DELETED_SOURCE_TIPS
        text += _truncate(rp_content)

    text += f"\n{SEPARATOR}\n"

    text += f"来源: {PLATFORM_NAME} {parsed.nickname or ''}\n"

    urls: list[str] = []
    rp_url = _detail_url(rp) if rp else ""
    if rp and rp_url:
        # 注意：bison 这里是中文冒号 + 无空格，与下面的英文冒号 + 空格不对称，属于原样复刻
        urls.append(f"转发详情：{rp_url}")
    post_url = _detail_url(parsed)
    if post_url:
        urls.append(f"详情: {post_url}")
    if urls:
        text += "\n".join(urls)

    return text


# ---------------------------------------------------------------- 九宫格拼图


def _check_image_square(size: tuple[int, int]) -> bool:
    """照抄 bison `_check_image_square`：宽高差 / 宽 < 5% 视为方图"""
    if len(size) < 2 or size[0] <= 0:
        return False
    return abs(size[0] - size[1]) / size[0] < 0.05


def _is_mergable(pics: list[str]) -> bool:
    """照抄 bison `is_pics_mergable`：全是 http(s) URL 才考虑合并"""
    return all(pic.startswith("http://") or pic.startswith("https://") for pic in pics)


def _open_image(data: bytes) -> PILImage:
    """线程池里解码图片（`load()` 提前触发解码，坏图在此处就抛出而非拼接时才炸）"""
    img = Image.open(BytesIO(data))
    img.load()
    return img


def _compose_grid(
    images: list[PILImage],
    matrix: tuple[int, int],
    x_coord: list[int],
    y_coord: list[int],
) -> bytes:
    """照抄 bison `pic_merge` 的拼接段：按坐标 paste 后存为 JPEG（同步，须在线程池执行）"""
    target = Image.new("RGB", (x_coord[-1], y_coord[-1]))
    for y in range(matrix[1]):
        for x in range(matrix[0]):
            source = images[y * matrix[0] + x]
            if source.mode != "RGB":
                # PIL 的 paste 本会隐式转换，这里显式处理以避免带 alpha 的图存 JPEG 报错
                source = source.convert("RGB")
            target.paste(
                source, (x_coord[x], y_coord[y], x_coord[x + 1], y_coord[y + 1])
            )
    buffer = BytesIO()
    target.save(buffer, "JPEG")
    return buffer.getvalue()


async def merge_pics(pic_urls: list[str]) -> list[bytes | str]:
    """配图合并（照抄 bison `pic_merge`）。

    ≥3 张、每张都是方图、同行等高同列等宽时拼成 3×N 大图（N=1/2/3），
    大图放在列表首位、其余原图 URL 原样跟随；任一条件不满足则原样返回 URL 列表。
    """
    pics = pic_urls
    if len(pics) < 3 or not _is_mergable(pics):
        return list(pics)

    loaded: dict[int, PILImage] = {}

    async def load(index: int) -> PILImage | None:
        """下载并解码第 index 张图；失败返回 None（不阻塞整体推送，退回原图 URL）"""
        if index in loaded:
            return loaded[index]
        try:
            data = await download_bytes(pics[index], proxy=plugin_config.proxy)
            image = await asyncio.to_thread(_open_image, data)
        except _PIC_ERRORS as e:
            logger.warning(
                f"配图下载/解码失败，放弃合并: {pics[index]} ({get_exc_desc(e)})"
            )
            return None
        loaded[index] = image
        return image

    first_image = await load(0)
    if first_image is None or not _check_image_square(first_image.size):
        return list(pics)

    images: list[PILImage] = [first_image]
    # 第一行：必须是方图且与首图等高
    for i in range(1, 3):
        cur_img = await load(i)
        if cur_img is None or not _check_image_square(cur_img.size):
            return list(pics)
        if cur_img.size[1] != images[0].size[1]:
            return list(pics)
        images.append(cur_img)

    _tmp = 0
    x_coord = [0]
    for i in range(3):
        _tmp += images[i].size[0]
        x_coord.append(_tmp)
    y_coord = [0, first_image.size[1]]

    async def process_row(row: int) -> bool:
        """尝试补齐第 row 行（0 起算）；任一校验不过返回 False，已拼好的行照常使用"""
        if len(pics) < (row + 1) * 3:
            return False
        row_first_img = await load(row * 3)
        if row_first_img is None or not _check_image_square(row_first_img.size):
            return False
        if row_first_img.size[0] != images[0].size[0]:
            return False
        image_row: list[PILImage] = [row_first_img]
        for i in range(row * 3 + 1, row * 3 + 3):
            cur_img = await load(i)
            if cur_img is None or not _check_image_square(cur_img.size):
                return False
            if cur_img.size[1] != row_first_img.size[1]:
                return False
            if cur_img.size[0] != images[i % 3].size[0]:
                return False
            image_row.append(cur_img)
        images.extend(image_row)
        y_coord.append(y_coord[-1] + row_first_img.size[1])
        return True

    matrix = (3, 1)
    if await process_row(1):
        matrix = (3, 2)
        # 第 2 行不成立时不再试第 3 行：bison 原实现此处会 images 索引越界，行为等价且不崩
        if await process_row(2):
            matrix = (3, 3)

    try:
        merged = await asyncio.to_thread(
            _compose_grid, images, matrix, x_coord, y_coord
        )
    except (OSError, ValueError) as e:
        logger.warning(f"配图拼接失败，退回原图列表: {get_exc_desc(e)}")
        return list(pics)

    logger.info(
        f"触发图片合并：{matrix[0]}×{matrix[1]}，合并 {matrix[0] * matrix[1]} 张"
    )
    rest: list[bytes | str] = list(pics[matrix[0] * matrix[1] :])
    rest.insert(0, merged)
    return rest


# ---------------------------------------------------------------- 消息段


async def build_messages(parsed: ParsedDynamic) -> list[MessageSegment]:
    """产出按 bison 拆包语义排好序的消息段：[文字卡片图, 图1, 图2, ...]。

    配图分组顺序照抄 basic 主题：先本动态配图、再转发源配图，**两组各自独立判断能否合并**。
    文字卡片渲染失败时降级为纯文本段：宁可丑不可漏。
    """
    text = build_text(parsed)
    segments: list[MessageSegment] = []

    try:
        card = await render_text(text)
    except Exception as e:
        # 渲染链路（浏览器 / 模板）任意环节失败都降级，不能因此漏推
        logger.opt(exception=True).warning(
            f"动态 {parsed.dyn_id} 文字卡片渲染失败，降级为纯文本: {get_exc_desc(e)}"
        )
        segments.append(MessageSegment.text(text))
    else:
        segments.append(image_segment(card))

    pics_group: list[list[str]] = []
    if parsed.pics:
        pics_group.append(list(parsed.pics))
    if parsed.repost and parsed.repost.pics:
        pics_group.append(list(parsed.repost.pics))

    for pics in pics_group:
        for pic in await merge_pics(pics):
            segments.append(image_segment(pic))

    return segments
