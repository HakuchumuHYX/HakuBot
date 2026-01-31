# stickers/overview.py
import math
import asyncio
from pathlib import Path
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.exception import FinishedException

# 导入模块而非变量，确保获取最新值
from .send import sticker_folders, resolve_folder_name, get_all_images_in_folder
from .config import IMAGE_EXTENSIONS, OVERVIEW_BATCH_SIZE, MAX_CANVAS_PIXELS
from . import send

# 注册命令
view_all_matcher = on_command("看所有", aliases={"查看所有", "view all"}, priority=5, block=True)
view_single_matcher = on_command("sticker", aliases={"看表情", "No.", "NO", "查看", "no", "no."}, priority=5,
                                 block=False)


def get_sort_key(file_path: Path):
    """排序键生成函数"""
    stem = file_path.stem
    if stem.isdigit():
        return (0, int(stem))
    return (1, file_path.name)


@view_single_matcher.handle()
async def handle_view_single(event: GroupMessageEvent, args: Message = CommandArg()):
    arg_text = args.extract_plain_text().strip()

    if not arg_text:
        await view_single_matcher.finish("请指定要查看的图片编号，例如：看表情 1024")

    # 分割参数
    id_list = arg_text.split()

    # 兼容旧逻辑：如果只有一个参数且是保留关键字，直接返回（交给其他 matcher 处理）
    if len(id_list) == 1 and id_list[0].lower() in ["stickers", "sticker", "表情", "表情包"]:
        return

    # 上限检查
    if len(id_list) > 5:
        await view_single_matcher.finish("一次最多只能查看 5 张图片哦。")

    # 动态获取当前最大编号
    current_max_id = send.current_max_id

    msg = Message()
    error_msgs = []
    has_valid_image = False

    for id_str in id_list:
        # 1. 基础格式校验
        if not id_str.isdigit():
            error_msgs.append(f"参数 '{id_str}' 不是合法的数字编号")
            continue

        target_id = int(id_str)

        # 2. 范围校验
        if target_id <= 0:
            error_msgs.append(f"编号 {target_id} 必须大于 0")
            continue

        if target_id > current_max_id:
            # 如果当前没有任何图片 (max_id=0)
            if current_max_id == 0:
                error_msgs.append(f"图库为空，无法查看编号 {target_id}")
            else:
                error_msgs.append(f"找不到编号 {target_id} (当前最大: {current_max_id})")
            continue

        # 3. 查找文件
        found_image_path: Optional[Path] = None

        for folder_name, folder_path in sticker_folders.items():
            if not folder_path.exists():
                continue

            for ext in IMAGE_EXTENSIONS:
                potential_path = folder_path / f"{target_id}{ext}"
                potential_path_upper = folder_path / f"{target_id}{ext.upper()}"

                if potential_path.exists() and potential_path.is_file():
                    found_image_path = potential_path
                    break
                elif potential_path_upper.exists() and potential_path_upper.is_file():
                    found_image_path = potential_path_upper
                    break

            if found_image_path:
                break

        # 4. 收集结果
        if found_image_path:
            try:
                # 累加图片消息段
                msg += MessageSegment.image(found_image_path)
                has_valid_image = True
            except Exception as e:
                logger.error(f"发送图片 {found_image_path} 失败: {e}")
                error_msgs.append(f"编号 {target_id} 图片加载失败")
        else:
            # 理论上 ID 在范围内但文件不存在（可能被手动删除了）
            error_msgs.append(f"编号 {target_id} 文件丢失")

    # 5. 发送最终结果
    if has_valid_image:
        # 如果有合法图片，先发图片，并在末尾附带错误信息（如果有）
        if error_msgs:
            msg += MessageSegment.text("\n⚠️ 部分编号未获取: " + " ".join(error_msgs))
        await view_single_matcher.finish(msg)
    elif error_msgs:
        # 如果一张图都没找到，只发错误信息
        await view_single_matcher.finish("\n".join(error_msgs))


@view_all_matcher.handle()
async def handle_view_all(event: GroupMessageEvent, args: Message = CommandArg()):
    folder_name = args.extract_plain_text().strip()

    if not folder_name:
        await view_all_matcher.finish("请指定要查看的文件夹名称，例如：看所有猫猫")

    actual_folder_name = resolve_folder_name(folder_name)
    if actual_folder_name not in sticker_folders:
        await view_all_matcher.finish(f"未找到文件夹 '{folder_name}'")

    folder_path = sticker_folders[actual_folder_name]

    # 使用公共函数获取所有图片
    try:
        image_files = get_all_images_in_folder(folder_path)
    except Exception as e:
        await view_all_matcher.finish(f"扫描文件夹失败: {e}")

    if not image_files:
        await view_all_matcher.finish(f"文件夹 '{actual_folder_name}' 中没有图片")

    # 按文件名(ID)排序
    image_files.sort(key=get_sort_key)

    total_count = len(image_files)

    batches = [image_files[i:i + OVERVIEW_BATCH_SIZE] for i in range(0, total_count, OVERVIEW_BATCH_SIZE)]
    total_pages = len(batches)

    if total_pages > 1:
        await view_all_matcher.send(
            f"文件夹 '{actual_folder_name}' 共 {total_count} 张图片，将分为 {total_pages} 张概览图发送，请稍候...")
    else:
        await view_all_matcher.send(f"正在生成 '{actual_folder_name}' 的概览，共 {total_count} 张图片，请稍候...")

    try:
        for i, batch in enumerate(batches):
            page_num = i + 1
            is_multi_page = total_pages > 1

            # 在线程中生成图片
            img_bytes = await asyncio.to_thread(
                render_gallery_overview_sync,
                batch,
                actual_folder_name,
                page_num if is_multi_page else None,
                total_pages if is_multi_page else None
            )

            if img_bytes:
                # 发送图片
                msg = MessageSegment.image(img_bytes)
                if is_multi_page:
                    msg += MessageSegment.text(f"\nPart {page_num}/{total_pages} ({len(batch)} items)")

                await view_all_matcher.send(msg)
            else:
                await view_all_matcher.send(f"第 {page_num} 页生成失败。")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"生成概览图出错: {e}")
        await view_all_matcher.finish(f"生成概览图时发生未知错误: {e}")


def render_gallery_overview_sync(image_files: List[Path], folder_name: str,
                                 page_num: Optional[int] = None, total_pages: Optional[int] = None) -> Optional[bytes]:
    """
    同步绘图函数 (CPU密集型)
    """
    total = len(image_files)
    if total == 0:
        return None

    # === 1. 动态布局计算 ===
    # 缩略图尺寸配置
    if total <= 20:
        thumb_size = 180;
        cols = 5;
        font_size = 24
    elif total <= 100:
        thumb_size = 120;
        cols = 8;
        font_size = 20
    elif total <= 500:
        thumb_size = 100;
        cols = 10;
        font_size = 16
    else:
        thumb_size = 80;
        cols = 15;
        font_size = 14

    # 间距配置
    padding = 10
    text_height = font_size + 10
    cell_w = thumb_size + padding
    cell_h = thumb_size + text_height + padding

    # 计算总宽高
    rows = math.ceil(total / cols)

    header_height = 60
    canvas_w = cols * cell_w + padding
    canvas_h = rows * cell_h + padding + header_height

    # 再次安全检查
    if canvas_w * canvas_h > MAX_CANVAS_PIXELS:
        logger.error(f"Canvas size too large: {canvas_w}x{canvas_h}")
        return None

    # === 2. 初始化画布 ===
    img = Image.new('RGB', (canvas_w, canvas_h), color=(245, 247, 250))
    draw = ImageDraw.Draw(img)

    # 加载字体
    try:
        title_font = ImageFont.truetype("msyh.ttc", 32)
        text_font = ImageFont.truetype("msyh.ttc", font_size)
    except:
        try:
            title_font = ImageFont.truetype("simhei.ttf", 32)
            text_font = ImageFont.truetype("simhei.ttf", font_size)
        except:
            title_font = ImageFont.load_default()
            text_font = ImageFont.load_default()

    # 绘制标题
    title_text = f"Gallery: {folder_name} ({total} items)"
    if page_num and total_pages:
        title_text += f" - Page {page_num}/{total_pages}"

    draw.text((20, 15), title_text, fill=(50, 50, 50), font=title_font)

    # === 3. 循环绘图 ===
    for idx, file_path in enumerate(image_files):
        row = idx // cols
        col = idx % cols

        x = padding + col * cell_w
        y = header_height + padding + row * cell_h

        try:
            with Image.open(file_path) as src_img:
                if src_img.mode != 'RGB':
                    src_img = src_img.convert('RGB')

                src_w, src_h = src_img.size
                ratio = min(thumb_size / src_w, thumb_size / src_h)
                new_w = int(src_w * ratio)
                new_h = int(src_h * ratio)

                resample_mode = Image.Resampling.BILINEAR if total > 200 else Image.Resampling.LANCZOS
                src_img = src_img.resize((new_w, new_h), resample_mode)

                paste_x = x + (thumb_size - new_w) // 2
                paste_y = y + (thumb_size - new_h) // 2
                img.paste(src_img, (paste_x, paste_y))

        except Exception as e:
            draw.rectangle([x, y, x + thumb_size, y + thumb_size], fill=(220, 220, 220))
            draw.text((x + 5, y + thumb_size // 2), "Error", fill="red", font=text_font)
            logger.error(f"Error loading {file_path}: {e}")

        # 绘制文件名 (ID)
        file_name = file_path.stem

        try:
            bbox = draw.textbbox((0, 0), file_name, font=text_font)
            text_w = bbox[2] - bbox[0]
        except:
            text_w = draw.textlength(file_name, font=text_font)

        text_x = x + (thumb_size - text_w) // 2
        text_y = y + thumb_size + 2

        draw.text((text_x, text_y), file_name, fill=(80, 80, 80), font=text_font)

    # === 4. 输出 ===
    output = BytesIO()
    img.save(output, format='JPEG', quality=85, optimize=True)
    return output.getvalue()
