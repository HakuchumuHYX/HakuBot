"""
Message rendering helpers for groupmate_waifu.

This module owns image generation, avatar message composition, and fallback
send/finish behavior. It should not mutate plugin state.
"""

from utils.rendering.fonts import load_font_from_path

import io
from typing import Iterable, Mapping, Sequence, Tuple

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.log import logger
from pil_utils import BuildImage, Text2Image

from utils.onebot.media import image_segment
from utils.onebot.forward import ForwardItem
from plugins.groupmate_waifu.utils import download_user_img


async def user_img(user_id: int) -> bytes:
    return await download_user_img(user_id)


async def user_img_segment(user_id: int) -> MessageSegment:
    try:
        return image_segment(await user_img(user_id))
    except Exception as e:
        logger.warning(f"获取用户头像失败，发送文本结果: user_id={user_id} error={e}")
        return MessageSegment.text("")


async def build_avatar_message(prefix: str, user_id: int, suffix: str = "") -> tuple:
    fallback = prefix + suffix
    message = prefix + (await user_img_segment(user_id)) + suffix
    return message, fallback


def _log_send_fallback(e: ActionFailed):
    logger.warning(
        f"发送图片消息失败，降级为文本: retcode={getattr(e, 'retcode', None)} "
        f"wording={getattr(e, 'wording', None)}"
    )


async def send_with_fallback(matcher, message, fallback: str, **kwargs):
    try:
        await matcher.send(message, **kwargs)
    except ActionFailed as e:
        _log_send_fallback(e)
        await matcher.send(fallback, **kwargs)


async def finish_with_fallback(matcher, message, fallback: str, **kwargs):
    try:
        await matcher.finish(message, **kwargs)
    except ActionFailed as e:
        _log_send_fallback(e)
        await matcher.finish(fallback, **kwargs)


def text_to_png(msg: str) -> io.BytesIO:
    output = io.BytesIO()
    try:
        text_img = Text2Image.from_text(msg, 50)
        text_img.wrap(800)
        img = text_img.to_image()

        bg_width = img.width + 40
        bg_height = img.height + 40
        bg = BuildImage.new("RGB", (bg_width, bg_height), "white")

        bg.paste(img, (20, 20), alpha=True)
        bg.image.save(output, format="png")

    except Exception as e:
        logger.error(f"text_to_png error: {e}")
        try:
            from PIL import Image, ImageDraw, ImageFont

            font_path = "msyh.ttc"
            try:
                font = load_font_from_path(font_path, 50)
            except IOError:
                logger.warning(f"找不到字体 {font_path}，使用默认字体。")
                font = ImageFont.load_default()

            dummy_img = Image.new("RGB", (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), msg, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            img = Image.new("RGB", (text_width + 40, text_height + 40), "white")
            draw = ImageDraw.Draw(img)
            draw.text((20, 20), msg, fill="black", font=font)
            img.save(output, format="png")

        except Exception as e2:
            logger.error(f"text_to_png fallback error: {e2}")
            raise Exception(f"无法生成图片: {e2}")

    return output


def image_from_text(msg: str) -> MessageSegment:
    return image_segment(text_to_png(msg))


def image_from_bbcode(msg: str) -> MessageSegment:
    return image_segment(bbcode_to_png(msg))


def render_protect_list(names: Iterable[str]) -> MessageSegment:
    return image_from_text("保护名单为：\n" + "\n".join(names))


def render_member_pool(
    title: str, members: Sequence[Mapping], limit: int = 80
) -> MessageSegment:
    msg = f"{title}：\n——————————————\n"
    msg += "\n".join(
        (member["card"] or member["nickname"]) for member in members[:limit]
    )
    return image_from_text(msg)


def render_cp_list(pairs: Iterable[Tuple[str, str]]) -> MessageSegment:
    msg = "".join(f"♥ {name_a} | {name_b}\n" for name_a, name_b in pairs)
    return image_from_text("本群CP：\n——————————————\n" + msg[:-1])


def make_forward_node(name: str, uin: int, content) -> ForwardItem:
    return ForwardItem(content=content, name=name, uin=uin)


def render_yinpa_record(
    title: str, rows: Iterable[Tuple[str, int]], action: str
) -> MessageSegment:
    msg = "\n".join(
        f"[align=left]{nickname}[/align][align=right]{action} {times} 次[/align]"
        for nickname, times in rows
    )
    return image_from_bbcode(f"{title}：\n——————————————\n" + msg)


def bbcode_to_png(msg: str, spacing: int = 10) -> io.BytesIO:
    output = io.BytesIO()
    try:
        text_img = Text2Image.from_bbcode_text(msg, 50)
        text_img.wrap(800)
        img = text_img.to_image()

        bg_width = img.width + 40
        bg_height = img.height + 40
        bg = BuildImage.new("RGB", (bg_width, bg_height), "white")

        bg.paste(img, (20, 20), alpha=True)
        bg.image.save(output, format="png")

    except Exception as e:
        logger.error(f"bbcode_to_png error: {e}")
        clean_msg = (
            msg.replace("[align=left]", "")
            .replace("[/align]", "")
            .replace("[align=right]", "")
        )
        return text_to_png(clean_msg)

    return output
