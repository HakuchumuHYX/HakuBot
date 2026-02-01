# stickers/overview.py
import math
import asyncio
import warnings
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

# 忽略 PIL 的 EXIF 损坏警告（不影响图片处理）
warnings.filterwarnings("ignore", message="Corrupt EXIF data")

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.exception import FinishedException

# 导入模块而非变量，确保获取最新值
from .send import sticker_folders, resolve_folder_name, get_all_images_in_folder
from .config import IMAGE_EXTENSIONS, OVERVIEW_BATCH_SIZE, MAX_CANVAS_PIXELS
from . import send

# === 字体缓存 ===
_font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def get_cached_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """获取缓存的字体，避免重复加载"""
    key = (font_name, size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(font_name, size)
        except:
            pass
    return _font_cache.get(key)


def load_fonts(font_size: int) -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """加载标题和正文字体"""
    # 尝试加载中文字体
    for font_name in ["msyh.ttc", "simhei.ttf", "Arial Unicode.ttf"]:
        title_font = get_cached_font(font_name, 32)
        text_font = get_cached_font(font_name, font_size)
        if title_font and text_font:
            return title_font, text_font
    # 回退到默认字体
    return ImageFont.load_default(), ImageFont.load_default()


# === 并行图片加载 ===
def load_and_resize_single(args: Tuple[Path, int, int]) -> Tuple[Path, Optional[Image.Image]]:
    """加载并缩放单张图片（用于线程池）"""
    file_path, thumb_size, resample_mode = args
    try:
        with Image.open(file_path) as src_img:
            # GIF 只取第一帧
            if hasattr(src_img, 'is_animated') and src_img.is_animated:
                src_img.seek(0)
            
            # 转换为 RGB
            if src_img.mode not in ('RGB', 'RGBA'):
                src_img = src_img.convert('RGB')
            elif src_img.mode == 'RGBA':
                # RGBA 需要处理透明背景
                background = Image.new('RGB', src_img.size, (255, 255, 255))
                background.paste(src_img, mask=src_img.split()[3])
                src_img = background
            
            # 计算缩放
            src_w, src_h = src_img.size
            ratio = min(thumb_size / src_w, thumb_size / src_h)
            new_w = int(src_w * ratio)
            new_h = int(src_h * ratio)
            
            # 缩放图片
            resized = src_img.resize((new_w, new_h), resample_mode)
            return (file_path, resized.copy())  # copy() 确保图片数据独立
    except Exception as e:
        logger.debug(f"Failed to load {file_path}: {e}")
        return (file_path, None)


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
    同步绘图函数 (CPU密集型，使用线程池并行加载图片)
    """
    total = len(image_files)
    if total == 0:
        return None

    # === 1. 动态布局计算 ===
    # 计算接近正方形的布局：让列数约等于 sqrt(total)
    cols = max(5, math.ceil(math.sqrt(total)))  # 最少5列
    rows = math.ceil(total / cols)

    # 根据总数动态调整缩略图尺寸
    if total <= 25:
        thumb_size = 200
        font_size = 24
    elif total <= 100:
        thumb_size = 160
        font_size = 20
    elif total <= 400:
        thumb_size = 120
        font_size = 16
    elif total <= 900:
        thumb_size = 100
        font_size = 14
    else:
        thumb_size = 80
        font_size = 12

    # 根据图片数量选择重采样模式（数量越多，使用越快的算法）
    if total <= 100:
        resample_mode = Image.Resampling.LANCZOS
    elif total <= 500:
        resample_mode = Image.Resampling.BILINEAR
    else:
        resample_mode = Image.Resampling.NEAREST

    # 间距配置
    padding = 10
    text_height = font_size + 10
    cell_w = thumb_size + padding
    cell_h = thumb_size + text_height + padding

    header_height = 60
    canvas_w = cols * cell_w + padding
    canvas_h = rows * cell_h + padding + header_height

    # 再次安全检查
    if canvas_w * canvas_h > MAX_CANVAS_PIXELS:
        logger.error(f"Canvas size too large: {canvas_w}x{canvas_h}")
        return None

    # === 2. 并行加载所有图片 ===
    # 根据图片数量动态调整线程数
    max_workers = min(16, max(4, total // 50))
    
    load_args = [(fp, thumb_size, resample_mode) for fp in image_files]
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(load_and_resize_single, load_args))
    
    # 构建 path -> resized_image 的映射
    loaded_images: Dict[Path, Optional[Image.Image]] = {path: img for path, img in results}

    # === 3. 初始化画布 ===
    canvas = Image.new('RGB', (canvas_w, canvas_h), color=(245, 247, 250))
    draw = ImageDraw.Draw(canvas)

    # 加载字体（使用缓存）
    title_font, text_font = load_fonts(font_size)

    # 绘制标题
    title_text = f"Gallery: {folder_name} ({total} items)"
    if page_num and total_pages:
        title_text += f" - Page {page_num}/{total_pages}"

    draw.text((20, 15), title_text, fill=(50, 50, 50), font=title_font)

    # === 4. 循环绘图（只做粘贴，不做IO） ===
    for idx, file_path in enumerate(image_files):
        row = idx // cols
        col = idx % cols

        x = padding + col * cell_w
        y = header_height + padding + row * cell_h

        resized_img = loaded_images.get(file_path)
        
        if resized_img:
            # 计算居中位置
            paste_x = x + (thumb_size - resized_img.width) // 2
            paste_y = y + (thumb_size - resized_img.height) // 2
            canvas.paste(resized_img, (paste_x, paste_y))
            # 释放内存
            resized_img.close()
        else:
            # 绘制错误占位符
            draw.rectangle([x, y, x + thumb_size, y + thumb_size], fill=(220, 220, 220))
            draw.text((x + 5, y + thumb_size // 2), "Error", fill="red", font=text_font)

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

    # === 5. 输出 ===
    output = BytesIO()
    canvas.save(output, format='JPEG', quality=85, optimize=True)
    return output.getvalue()
