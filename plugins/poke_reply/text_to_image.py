# poke_reply/services/text_to_image.py
from nonebot import logger
import asyncio
from typing import Tuple

# vvvvvv 【修改：导入路径】 vvvvvv
from .config import (
    TEXT_TO_IMAGE_ENABLED_GROUPS,
    TEXT_TO_IMAGE_COMMAND_PRIORITY,
    add_text_to_image_group,
    remove_text_to_image_group,
    is_text_to_image_enabled,
    set_text_to_image_threshold,
    get_text_to_image_threshold
)
from .common import get_group_id

try:
    from nonebot_plugin_htmlrender import text_to_pic
    HTMLRENDER_AVAILABLE = True
except ImportError:
    logger.warning("nonebot-plugin-htmlrender 未安装，文本转图片功能将使用备用方案")
    HTMLRENDER_AVAILABLE = False


async def convert_text_to_image(text: str, group_id: int) -> Tuple[bool, bytes]:
    """
    将文本转换为图片
    """
    try:
        if HTMLRENDER_AVAILABLE:
            try:
                image_data = await text_to_pic(text)
                return True, image_data
            except Exception as e:
                logger.warning(f"htmlrender 简单调用失败: {e}，尝试使用 PIL 备用方案")
                return await fallback_text_to_image(text)
        else:
            return await fallback_text_to_image(text)
    except Exception as e:
        logger.error(f"群 {group_id} 文本转图片失败: {e}")
        return False, b""


async def fallback_text_to_image(text: str) -> Tuple[bool, bytes]:
    """备用文本转图片方案（使用PIL）"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        font_size = 20
        line_spacing = 10
        margin = 40
        max_width = 800

        try:
            font = ImageFont.truetype("msyh.ttc", font_size)
        except:
            try:
                font = ImageFont.truetype("simhei.ttf", font_size)
            except:
                font = ImageFont.load_default()

        lines = []
        current_line = ""
        for char in text:
            test_line = current_line + char
            bbox = font.getbbox(test_line)
            text_width = bbox[2] - bbox[0]
            if text_width <= (max_width - 2 * margin) or not current_line:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)

        line_height = font_size + line_spacing
        img_height = len(lines) * line_height + 2 * margin

        img = Image.new('RGB', (max_width, img_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        y = margin
        for line in lines:
            draw.text((margin, y), line, fill=(0, 0, 0), font=font)
            y += line_height

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return True, img_bytes.getvalue()
    except Exception as e:
        logger.error(f"备用文本转图片方案失败: {e}")
        return False, b""