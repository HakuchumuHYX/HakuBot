# stickers/help.py
import io
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import FinishedException
from typing import Tuple

# 尝试导入 htmlrender
try:
    from nonebot_plugin_htmlrender import text_to_pic

    HTMLRENDER_AVAILABLE = True
except ImportError:
    logger.warning("stickers-help: 未安装 nonebot-plugin-htmlrender，将使用 PIL 备用方案")
    HTMLRENDER_AVAILABLE = False

# 注册帮助命令
help_matcher = on_command(
    "sticker帮助",
    aliases={"sticker help", "stickers help", "stickers帮助"},
    priority=5,
    block=True
)

# 帮助文档文本
HELP_TEXT = """
Stickers 插件帮助文档
---------------------------------

[用户功能]

1.  发送随机表情 (单张)
    -   随机<文件夹名>
    -   示例: `随机猫猫`

2.  发送随机表情 (多张)
    -   随机<文件夹名> x <n>
    -   (n最大为5，分隔符支持 x, *, 乘)
    -   示例: 随机猫猫 x 3

3.  发送全局随机表情
    -   随机stickers (或 随机sticker, 随机表情)
    -   功能: 从所有文件夹中随机抽取一张图片。

4.  发送全局随机表情 (多张)
    -   随机stickers x <n> (或 随机表情 x <n>)
    -   功能: 随机抽取n次图片 。
    -   示例: 随机表情 x 5

5.  查看表情列表
    -   查看stickers
    -   功能: 以图片形式显示所有文件夹、别名和图片数量。

6.  投稿
    -   <文件夹名>投稿
    -   功能: 发送图片并附带此命令，可投稿至指定文件夹。
    -   备注: 也可通过回复一条图片消息并发送此命令来投稿。

7.  强制投稿
    -   <文件夹名>投稿 force
    -   功能: 强制上传图片，跳过查重检查。

8.  添加别名
    -   添加别名 <别名> to <文件夹名>
    -   示例: 添加别名 猫咪 to 猫猫
"""


@help_matcher.handle()
async def handle_help():
    """处理帮助命令，发送图片"""
    try:
        success, image_data = await convert_text_to_image(HELP_TEXT)
        if success and image_data:
            await help_matcher.finish(MessageSegment.image(image_data))
        else:
            logger.error("stickers-help: 帮助图片生成失败，回退到文本")
            await help_matcher.finish(HELP_TEXT)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"stickers-help: 发送帮助时出错: {e}")
        await help_matcher.finish("获取帮助信息时出错，请查看日志")


async def convert_text_to_image(text: str) -> Tuple[bool, bytes]:
    """
    将文本转换为图片 (复用 poke_reply 的逻辑)
    """
    try:
        if HTMLRENDER_AVAILABLE:
            try:
                # 使用 text_to_pic，它通常会使用 <pre> 标签保留格式
                image_data = await text_to_pic(text)
                return True, image_data
            except Exception as e:
                logger.warning(f"stickers-help: htmlrender 调用失败: {e}，尝试 PIL")
                return await fallback_text_to_image(text)
        else:
            return await fallback_text_to_image(text)
    except Exception as e:
        logger.error(f"stickers-help: 文本转图片失败: {e}")
        return False, b""


async def fallback_text_to_image(text: str) -> Tuple[bool, bytes]:
    """备用文本转图片方案（使用PIL）"""
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_size = 20
        line_spacing = 10
        margin = 40
        max_width = 800  # 适当加宽以容纳帮助文本

        try:
            # 优先使用 'msyh.ttc' (微软雅黑)
            font = ImageFont.truetype("msyh.ttc", font_size)
        except:
            try:
                # 备用 'simhei.ttf' (黑体)
                font = ImageFont.truetype("simhei.ttf", font_size)
            except:
                # 最终备用
                font = ImageFont.load_default()

        # 分割文本为行
        text_lines = text.strip().split('\n')

        lines = []
        for text_line in text_lines:
            # (这个备用方案不支持复杂的自动换行，仅按 \n 分割)
            lines.append(text_line)

        # 计算图片高度
        line_height = font_size + line_spacing
        img_height = len(lines) * line_height + 2 * margin

        # 动态计算最大宽度
        max_line_width = 0
        for line in lines:
            try:
                # 检查 PIL 版本
                if hasattr(font, "getbbox"):
                    bbox = font.getbbox(line)
                    text_width = bbox[2] - bbox[0]
                else:
                    # 兼容旧版 PIL
                    text_width, _ = font.getsize(line)
                max_line_width = max(max_line_width, text_width)
            except Exception:
                # 兼容 load_default()
                max_line_width = max(max_line_width, len(line) * font_size * 0.6)

        img_width = max(max_width, max_line_width + 2 * margin)

        # 创建画布
        img = Image.new('RGB', (img_width, img_height), color=(255, 255, 255))
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
        logger.error(f"stickers-help: 备用文本转图片方案失败: {e}")
        return False, b""