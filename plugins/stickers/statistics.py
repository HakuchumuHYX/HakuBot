# statistics.py
import random
import math
from pathlib import Path
from typing import Dict, List, Tuple
from PIL import Image, ImageDraw, ImageFont
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger
from .send import sticker_folders, count_images_in_folder, get_random_sticker, get_folder_display_info


def calculate_cell_height(folder_info: Dict) -> int:
    """
    根据文件夹信息计算单元格高度

    返回: 单元格高度
    """
    base_height = 190  # 基础高度
    aliases = folder_info.get("aliases", [])

    if not aliases:
        return base_height  # 没有别名，使用基础高度

    # 计算别名需要的额外高度
    alias_lines = 1
    alias_text = ", ".join(aliases)

    # 简单估算文本长度，每20个字符可能需要换行
    if len(alias_text) > 20:
        alias_lines = 2
    if len(alias_text) > 40:
        alias_lines = 3

    # 每行别名增加15px高度
    extra_height = (alias_lines - 1) * 15

    return base_height + extra_height


def get_max_cell_height(folder_info_list: List[Dict]) -> int:
    """
    获取所有单元格中的最大高度

    返回: 最大单元格高度
    """
    max_height = 190  # 最小高度

    for folder_info in folder_info_list:
        height = calculate_cell_height(folder_info)
        if height > max_height:
            max_height = height

    return max_height


def draw_multiline_text(draw, text: str, font, max_width: int, x: int, y: int,
                        fill: Tuple, max_lines: int = 3) -> int:
    """
    绘制多行文本，自动换行

    返回: 绘制的行数
    """
    words = text.split(',')
    lines = []
    current_line = []

    for word in words:
        word = word.strip()
        if not word:
            continue

        test_line = ', '.join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        text_width = bbox[2] - bbox[0]

        if text_width <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(', '.join(current_line))
                current_line = [word]
            else:
                # 单个词就超过宽度，强制分割
                lines.append(word)
                current_line = []

            if len(lines) >= max_lines:
                break

    if current_line and len(lines) < max_lines:
        lines.append(', '.join(current_line))

    # 如果超过最大行数，处理最后一行
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines[-1]:
            last_line = lines[-1]
            while last_line:
                test_text = last_line + "..."
                bbox = draw.textbbox((0, 0), test_text, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    lines[-1] = test_text
                    break
                last_line = last_line[:-1]

    # 绘制每一行
    line_height = font.size + 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        line_x = x + (max_width - line_width) // 2
        draw.text((line_x, y + i * line_height), line, fill=fill, font=font)

    return len(lines)


async def render_stickers_preview() -> bytes:
    """
    使用PIL渲染贴图预览图片

    返回: 图片的bytes数据
    """
    try:
        folder_info_list = get_folder_display_info()
        if not folder_info_list:
            return None

        # 按文件夹名称排序
        sorted_folders = sorted(folder_info_list, key=lambda x: x["name"])

        # 计算布局
        cols = min(7, len(sorted_folders))  # 每行最多7个
        rows = math.ceil(len(sorted_folders) / cols)

        # 动态计算单元格高度
        cell_height = get_max_cell_height(sorted_folders) + 25
        cell_width = 220
        padding = 20
        spacing = 15

        # 计算总尺寸
        img_width = cols * cell_width + (cols - 1) * spacing + 2 * padding
        img_height = rows * cell_height + (rows - 1) * spacing + 2 * padding + 110

        # 创建画布
        img = Image.new('RGB', (img_width, img_height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        # 保持字体不变
        try:
            title_font = ImageFont.truetype("msyh.ttc", 38)
            name_font = ImageFont.truetype("msyhbd.ttc", 24)
            count_font = ImageFont.truetype("msyh.ttc", 20)
            alias_font = ImageFont.truetype("msyh.ttc", 16)
        except:
            try:
                title_font = ImageFont.truetype("simhei.ttf", 38)
                name_font = ImageFont.truetype("simhei.ttf", 24)
                count_font = ImageFont.truetype("simhei.ttf", 20)
                alias_font = ImageFont.truetype("simhei.ttf", 16)
            except:
                title_font = ImageFont.load_default()
                name_font = ImageFont.load_default()
                count_font = ImageFont.load_default()
                alias_font = ImageFont.load_default()

        # 绘制标题
        title = "贴图库预览"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (img_width - title_width) // 2
        draw.text((title_x, padding), title, fill=(44, 62, 80), font=title_font)

        # 绘制统计信息
        total_folders = len(sorted_folders)
        total_images = sum(folder_info["image_count"] for folder_info in sorted_folders)
        stats_text = f"{total_folders}个文件夹  {total_images}张图片"
        stats_bbox = draw.textbbox((0, 0), stats_text, font=name_font)
        stats_width = stats_bbox[2] - stats_bbox[0]
        stats_x = (img_width - stats_width) // 2
        draw.text((stats_x, padding + 45), stats_text, fill=(52, 152, 219), font=name_font)

        # 绘制网格
        start_y = padding + 85

        for i, folder_info in enumerate(sorted_folders):
            row = i // cols
            col = i % cols

            # 计算单元格位置
            x = padding + col * (cell_width + spacing)
            y = start_y + row * (cell_height + spacing)

            # 绘制卡片背景
            draw.rounded_rectangle(
                [x, y, x + cell_width, y + cell_height],
                radius=10,
                fill=(255, 255, 255),
                outline=(225, 232, 237),
                width=2
            )

            # 动态计算当前单元格的布局参数
            current_cell_height = calculate_cell_height(folder_info)
            aliases = folder_info.get("aliases", [])

            # 图片区域高度：至少占单元格的60%
            image_area_height = int(cell_height * 0.6)

            # 动态调整间距
            if not aliases:
                # 没有别名，增加名称和数量之间的间距
                image_top_margin = 10
                image_to_name_margin = 15
                name_to_count_margin = 10
            else:
                # 有别名，压缩其他间距
                image_top_margin = 5
                image_to_name_margin = 8
                name_to_alias_margin = 2
                alias_to_count_margin = 3

            # 获取预览图片
            preview_path = get_random_sticker(folder_info["name"])

            if preview_path and preview_path.exists():
                try:
                    preview_image = Image.open(preview_path)
                    if preview_image.mode != 'RGB':
                        preview_image = preview_image.convert('RGB')

                    # 缩放图片以适应图片区域
                    preview_width, preview_height = preview_image.size
                    target_height = image_area_height - 15  # 保留边距
                    target_width = cell_width - 20  # 保留边距

                    # 计算缩放比例，保持宽高比
                    ratio = min(target_width / preview_width, target_height / preview_height)
                    new_width = int(preview_width * ratio)
                    new_height = int(preview_height * ratio)

                    preview_image = preview_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    # 居中放置图片
                    img_x = x + (cell_width - new_width) // 2
                    img_y = y + image_top_margin + (image_area_height - new_height) // 2

                    img.paste(preview_image, (img_x, img_y))

                except Exception:
                    # 绘制占位符
                    placeholder_size = min(60, int(image_area_height * 0.6))
                    placeholder_x = x + (cell_width - placeholder_size) // 2
                    placeholder_y = y + image_top_margin + (image_area_height - placeholder_size) // 2

                    # 绘制占位符背景
                    draw.rounded_rectangle(
                        [placeholder_x, placeholder_y, placeholder_x + placeholder_size,
                         placeholder_y + placeholder_size],
                        radius=8,
                        fill=(225, 232, 237)
                    )

                    # 绘制文件夹图标
                    folder_text = "文件夹"
                    try:
                        icon_font = ImageFont.truetype("msyh.ttc", 14)
                    except:
                        icon_font = ImageFont.load_default()

                    text_bbox = draw.textbbox((0, 0), folder_text, font=icon_font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]
                    text_x = placeholder_x + (placeholder_size - text_width) // 2
                    text_y = placeholder_y + (placeholder_size - text_height) // 2
                    draw.text((text_x, text_y), folder_text, fill=(127, 140, 141), font=icon_font)
            else:
                # 绘制占位符
                placeholder_size = min(60, int(image_area_height * 0.6))
                placeholder_x = x + (cell_width - placeholder_size) // 2
                placeholder_y = y + image_top_margin + (image_area_height - placeholder_size) // 2

                # 绘制占位符背景
                draw.rounded_rectangle(
                    [placeholder_x, placeholder_y, placeholder_x + placeholder_size, placeholder_y + placeholder_size],
                    radius=8,
                    fill=(225, 232, 237)
                )

                # 绘制文件夹图标
                folder_text = "文件夹"
                try:
                    icon_font = ImageFont.truetype("msyh.ttc", 14)
                except:
                    icon_font = ImageFont.load_default()

                text_bbox = draw.textbbox((0, 0), folder_text, font=icon_font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                text_x = placeholder_x + (placeholder_size - text_width) // 2
                text_y = placeholder_y + (placeholder_size - text_height) // 2
                draw.text((text_x, text_y), folder_text, fill=(127, 140, 141), font=icon_font)

            # 绘制文件夹名称
            name_y = y + image_top_margin + image_area_height + image_to_name_margin
            folder_name_text = folder_info["name"][:12] + "..." if len(folder_info["name"]) > 12 else folder_info[
                "name"]
            name_bbox = draw.textbbox((0, 0), folder_name_text, font=name_font)
            name_width = name_bbox[2] - name_bbox[0]

            # 如果名称太长，进一步截断
            if name_width > cell_width - 20:
                max_chars = min(len(folder_name_text), 8)
                folder_name_text = folder_name_text[:max_chars] + "..."
                name_bbox = draw.textbbox((0, 0), folder_name_text, font=name_font)
                name_width = name_bbox[2] - name_bbox[0]

            name_x = x + (cell_width - name_width) // 2
            draw.text((name_x, name_y), folder_name_text, fill=(44, 62, 80), font=name_font)

            # 绘制别名（如果有）
            aliases = folder_info.get("aliases", [])
            if aliases:
                alias_text = f"别名: {', '.join(aliases)}"
                alias_y = name_y + name_to_alias_margin + 24

                # 使用多行文本绘制别名
                alias_lines = draw_multiline_text(
                    draw, alias_text, alias_font,
                    cell_width - 20,  # 最大宽度
                    x + 10, alias_y,  # 位置
                    (127, 140, 141),  # 颜色
                    max_lines=3  # 最多3行
                )
            else:
                alias_lines = 0

            # 绘制图片数量
            image_count = folder_info["image_count"]
            count_text = f"{image_count} 张"
            count_bbox = draw.textbbox((0, 0), count_text, font=count_font)
            count_width = count_bbox[2] - count_bbox[0]
            count_x = x + (cell_width - count_width) // 2

            # 根据是否有别名及别名行数调整位置
            if aliases:
                count_y = alias_y + alias_to_count_margin + (alias_lines * 18)  # 每行约18px
            else:
                count_y = name_y + 30  # 没有别名时增加间距

            draw.text((count_x, count_y), count_text, fill=(127, 140, 141), font=count_font)

        # 绘制底部说明
        footer_text = "Data Provided by LunaBot Gallery (by @NeuraXmy) and astrbot_plugin_stickers (by @shiywhh)."
        footer_bbox = draw.textbbox((0, 0), footer_text, font=count_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
        footer_x = (img_width - footer_width) // 2
        footer_y = img_height - padding - 12
        draw.text((footer_x, footer_y), footer_text, fill=(127, 140, 141), font=count_font)

        # 转换为bytes
        from io import BytesIO
        img_bytes = BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        logger.error(f"生成贴图预览图片失败: {e}")
        return None


def get_sticker_statistics() -> str:
    """
    获取贴图文件夹统计信息

    返回: 格式化的统计信息字符串
    """
    folder_info_list = get_folder_display_info()
    if not folder_info_list:
        return "当前没有可用的贴图文件夹"

    # 按文件夹名称排序
    sorted_folders = sorted(folder_info_list, key=lambda x: x["name"])

    # 构建统计信息
    lines = ["当前stickers列表："]

    for folder_info in sorted_folders:
        folder_name = folder_info["name"]
        image_count = folder_info["image_count"]
        aliases = folder_info.get("aliases", [])

        if aliases:
            alias_text = f" (别名: {', '.join(aliases)})"
        else:
            alias_text = ""

        lines.append(f"{folder_name}{alias_text}：{image_count}张")

    # 添加总计信息
    total_folders = len(sorted_folders)
    total_images = sum(folder_info["image_count"] for folder_info in sorted_folders)
    lines.append(f"\n总计：{total_folders}个文件夹，{total_images}张图片")

    return "\n".join(lines)


def handle_statistics_command(message_text: str) -> bool:
    """
    检查消息是否为查看统计命令

    返回: 是否为统计命令
    """
    return message_text.strip() == "查看stickers"