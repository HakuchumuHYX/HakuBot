# statistics.py
import random
import math
from pathlib import Path
from typing import Dict, List, Tuple
from PIL import Image, ImageDraw, ImageFont
from nonebot.adapters.onebot.v11 import MessageSegment
from .send import sticker_folders, count_images_in_folder, get_random_sticker


async def render_stickers_preview() -> bytes:
    """
    使用PIL渲染贴图预览图片

    返回: 图片的bytes数据
    """
    try:
        if not sticker_folders:
            return None

        # 获取所有文件夹并按名称排序
        sorted_folders = sorted(sticker_folders.keys())

        # 计算布局
        cols = min(6, len(sorted_folders))  # 每行最多6个
        rows = math.ceil(len(sorted_folders) / cols)

        # 保持单元格尺寸不变
        cell_width = 220
        cell_height = 190
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
        except:
            try:
                title_font = ImageFont.truetype("simhei.ttf", 38)
                name_font = ImageFont.truetype("simhei.ttf", 24)
                count_font = ImageFont.truetype("simhei.ttf", 20)
            except:
                title_font = ImageFont.load_default()
                name_font = ImageFont.load_default()
                count_font = ImageFont.load_default()

        # 绘制标题
        title = "贴图库预览"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (img_width - title_width) // 2
        draw.text((title_x, padding), title, fill=(44, 62, 80), font=title_font)

        # 绘制统计信息
        total_folders = len(sorted_folders)
        total_images = sum(count_images_in_folder(folder) for folder in sticker_folders)
        stats_text = f"{total_folders}个文件夹  {total_images}张图片"
        stats_bbox = draw.textbbox((0, 0), stats_text, font=name_font)
        stats_width = stats_bbox[2] - stats_bbox[0]
        stats_x = (img_width - stats_width) // 2
        draw.text((stats_x, padding + 45), stats_text, fill=(52, 152, 219), font=name_font)

        # 绘制网格
        start_y = padding + 85

        for i, folder_name in enumerate(sorted_folders):
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

            # 获取预览图片
            preview_path = get_random_sticker(folder_name)

            # 计算元素间距 - 基于120px图片高度重新分配
            # 单元格总高度：190px
            # 图片区域高度：120px
            # 文件夹名称区域高度：24px
            # 图片数量区域高度：20px
            # 剩余空间：190 - 120 - 24 - 20 = 26px
            # 平均分配为4个间隙：26 / 4 = 6.5px ≈ 7px
            image_area_height = 120
            image_top_margin = 7
            image_to_name_margin = 6  # 稍微调整以平衡
            name_to_count_margin = 7
            count_bottom_margin = 6  # 稍微调整以平衡

            if preview_path and preview_path.exists():
                try:
                    preview_image = Image.open(preview_path)
                    if preview_image.mode != 'RGB':
                        preview_image = preview_image.convert('RGB')

                    # 缩放图片以适应120px高度区域
                    preview_width, preview_height = preview_image.size
                    target_height = image_area_height - 10  # 保留少量边距
                    target_width = cell_width - 20  # 保留少量边距

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
                    placeholder_size = 60
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
                        icon_font = ImageFont.truetype("msyh.ttc", 16)
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
                placeholder_size = 60
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
                    icon_font = ImageFont.truetype("msyh.ttc", 16)
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
            folder_name_text = folder_name[:10] + "..." if len(folder_name) > 10 else folder_name
            name_bbox = draw.textbbox((0, 0), folder_name_text, font=name_font)
            name_width = name_bbox[2] - name_bbox[0]

            # 如果名称太长，进一步截断
            if name_width > cell_width - 20:
                max_chars = min(len(folder_name_text), 7)
                folder_name_text = folder_name_text[:max_chars] + "..."
                name_bbox = draw.textbbox((0, 0), folder_name_text, font=name_font)
                name_width = name_bbox[2] - name_bbox[0]

            name_x = x + (cell_width - name_width) // 2
            draw.text((name_x, name_y), folder_name_text, fill=(44, 62, 80), font=name_font)

            # 绘制图片数量
            image_count = count_images_in_folder(folder_name)
            count_text = f"{image_count} 张"
            count_bbox = draw.textbbox((0, 0), count_text, font=count_font)
            count_width = count_bbox[2] - count_bbox[0]
            count_x = x + (cell_width - count_width) // 2
            count_y = name_y + name_to_count_margin + 24  # 24是文件夹名称的近似高度
            draw.text((count_x, count_y), count_text, fill=(127, 140, 141), font=count_font)

        # 绘制底部说明
        footer_text = "Data provided by LunaBot Gallery and astrbot_plugin_stickers."
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
        print(f"生成贴图预览图片失败: {e}")
        return None


def get_sticker_statistics() -> str:
    """
    获取贴图文件夹统计信息

    返回: 格式化的统计信息字符串
    """
    if not sticker_folders:
        return "当前没有可用的贴图文件夹"

    # 获取所有文件夹并按名称排序
    sorted_folders = sorted(sticker_folders.keys())

    # 构建统计信息
    lines = ["当前stickers列表："]

    for folder_name in sorted_folders:
        image_count = count_images_in_folder(folder_name)
        lines.append(f"{folder_name}：{image_count}张")

    # 添加总计信息
    total_folders = len(sticker_folders)
    total_images = sum(count_images_in_folder(folder) for folder in sticker_folders)
    lines.append(f"\n总计：{total_folders}个文件夹，{total_images}张图片")

    return "\n".join(lines)


def handle_statistics_command(message_text: str) -> bool:
    """
    检查消息是否为查看统计命令

    返回: 是否为统计命令
    """
    return message_text.strip() == "查看stickers"