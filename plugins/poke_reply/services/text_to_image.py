# services/text_to_image.py
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.rule import to_me
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg
import asyncio
from typing import Tuple

from ..config import (
    TEXT_TO_IMAGE_ENABLED_GROUPS,
    TEXT_TO_IMAGE_COMMAND_PRIORITY,
    add_text_to_image_group,
    remove_text_to_image_group,
    is_text_to_image_enabled,
    set_text_to_image_threshold,
    get_text_to_image_threshold
)
from ..utils.common import get_group_id

# 注册命令处理器
enable_text_to_image = on_command("启用文本转图片", permission=SUPERUSER, rule=to_me(),
                                  priority=TEXT_TO_IMAGE_COMMAND_PRIORITY, block=True)
disable_text_to_image = on_command("禁用文本转图片", permission=SUPERUSER, rule=to_me(),
                                   priority=TEXT_TO_IMAGE_COMMAND_PRIORITY, block=True)
text_to_image_status = on_command("文本转图片状态", permission=SUPERUSER, rule=to_me(),
                                  priority=TEXT_TO_IMAGE_COMMAND_PRIORITY, block=True)
set_text_threshold = on_command("设置文本阈值", permission=SUPERUSER, rule=to_me(),
                                priority=TEXT_TO_IMAGE_COMMAND_PRIORITY, block=True)

try:
    from nonebot_plugin_htmlrender import text_to_pic

    HTMLRENDER_AVAILABLE = True
except ImportError:
    logger.warning("nonebot-plugin-htmlrender 未安装，文本转图片功能将使用备用方案")
    HTMLRENDER_AVAILABLE = False


async def convert_text_to_image(text: str, group_id: int) -> Tuple[bool, bytes]:
    """
    将文本转换为图片

    Args:
        text: 要转换的文本
        group_id: 群组ID（用于日志）

    Returns:
        Tuple[bool, bytes]: (是否成功, 图片数据)
    """
    try:
        if HTMLRENDER_AVAILABLE:
            # 使用 htmlrender 插件转换 - 修复参数问题
            try:
                # 先尝试最简单的无参数调用
                image_data = await text_to_pic(text)
                return True, image_data
            except Exception as e:
                logger.warning(f"htmlrender 简单调用失败: {e}，尝试使用 PIL 备用方案")
                return await fallback_text_to_image(text)
        else:
            # 备用方案：使用 PIL 生成图片
            return await fallback_text_to_image(text)
    except Exception as e:
        logger.error(f"群 {group_id} 文本转图片失败: {e}")
        return False, b""


async def fallback_text_to_image(text: str) -> Tuple[bool, bytes]:
    """备用文本转图片方案（使用PIL）"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io

        # 基本配置
        font_size = 20
        line_spacing = 10
        margin = 40
        max_width = 800

        # 计算文本尺寸
        try:
            # 尝试使用系统字体
            font = ImageFont.truetype("msyh.ttc", font_size)
        except:
            try:
                # 尝试其他常见字体
                font = ImageFont.truetype("simhei.ttf", font_size)
            except:
                # 使用默认字体
                font = ImageFont.load_default()

        # 分割文本行
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

        # 计算图片高度
        line_height = font_size + line_spacing
        img_height = len(lines) * line_height + 2 * margin

        # 创建图片
        img = Image.new('RGB', (max_width, img_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        # 绘制文本
        y = margin
        for line in lines:
            draw.text((margin, y), line, fill=(0, 0, 0), font=font)
            y += line_height

        # 转换为 bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return True, img_bytes.getvalue()

    except Exception as e:
        logger.error(f"备用文本转图片方案失败: {e}")
        return False, b""


@enable_text_to_image.handle()
async def handle_enable_text_to_image(event: GroupMessageEvent, args: Message = CommandArg()):
    """启用文本转图片功能"""
    group_id = event.group_id
    arg_text = args.extract_plain_text().strip()

    try:
        if arg_text:
            # 如果指定了阈值，更新阈值
            new_threshold = int(arg_text)
            set_text_to_image_threshold(new_threshold)
            add_text_to_image_group(group_id)
            await enable_text_to_image.finish(f"已启用文本转图片功能，阈值设置为 {new_threshold} 字符喵！")
        else:
            add_text_to_image_group(group_id)
            await enable_text_to_image.finish(
                f"已启用文本转图片功能，当前阈值为 {get_text_to_image_threshold()} 字符喵！")
    except ValueError:
        await enable_text_to_image.finish("阈值必须是数字喵！")


@disable_text_to_image.handle()
async def handle_disable_text_to_image(event: GroupMessageEvent):
    """禁用文本转图片功能"""
    group_id = event.group_id
    remove_text_to_image_group(group_id)
    await disable_text_to_image.finish("已禁用文本转图片功能喵！")


@text_to_image_status.handle()
async def handle_text_to_image_status(event: GroupMessageEvent):
    """查看文本转图片状态"""
    group_id = event.group_id
    enabled = is_text_to_image_enabled(group_id)

    status_msg = "启用" if enabled else "禁用"
    message = (
        f"文本转图片功能状态：{status_msg}\n"
        f"当前阈值：{get_text_to_image_threshold()} 字符\n"
        f"渲染引擎：{'htmlrender' if HTMLRENDER_AVAILABLE else 'PIL备用方案'}"
    )

    await text_to_image_status.finish(message)


@set_text_threshold.handle()
async def handle_set_text_threshold(event: GroupMessageEvent, args: Message = CommandArg()):
    """设置文本长度阈值"""
    arg_text = args.extract_plain_text().strip()

    try:
        if not arg_text:
            await set_text_threshold.finish(f"当前文本转图片阈值为 {get_text_to_image_threshold()} 字符喵！")
            return

        new_threshold = int(arg_text)
        if new_threshold < 50:
            await set_text_threshold.finish("阈值不能小于50字符喵！")
            return

        set_text_to_image_threshold(new_threshold)
        await set_text_threshold.finish(f"已设置文本转图片阈值为 {new_threshold} 字符喵！")

    except ValueError:
        await set_text_threshold.finish("阈值必须是数字喵！")