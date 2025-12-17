# image_symmetry.py
import os
import tempfile
import aiohttp
from PIL import Image, ImageSequence
from pathlib import Path
import numpy as np
from nonebot.log import logger


async def download_image(url: str) -> str:
    """下载图片到临时目录"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                temp_dir = tempfile.gettempdir()
                # 根据URL猜测文件扩展名
                if '.gif' in url.lower():
                    file_ext = '.gif'
                elif '.png' in url.lower():
                    file_ext = '.png'
                else:
                    file_ext = '.jpg'

                temp_path = os.path.join(temp_dir, f"temp_img_{os.urandom(4).hex()}{file_ext}")

                content = await response.read()
                with open(temp_path, 'wb') as f:
                    f.write(content)

                return temp_path
            else:
                raise Exception(f"下载图片失败: {response.status}")


async def safe_delete_file(file_path: str, max_retries: int = 3):
    """安全删除文件"""
    for i in range(max_retries):
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                return True
        except PermissionError:
            if i < max_retries - 1:
                import time
                time.sleep(0.1)
            else:
                return False
    return False


def convert_to_supported_mode(img):
    """将图像转换为支持的格式，正确处理透明度"""
    if img.mode == 'P':
        # 检查调色板图像是否有透明度
        if 'transparency' in img.info:
            # 有透明度的调色板图像转换为RGBA
            return img.convert('RGBA')
        else:
            # 无透明度的调色板图像转换为RGB
            return img.convert('RGB')
    elif img.mode == 'LA':
        # 灰度+透明度转换为RGBA
        return img.convert('RGBA')
    elif img.mode not in ['RGB', 'RGBA']:
        return img.convert('RGB')
    return img


async def process_left_symmetry(image_path: str) -> Image.Image:
    """左对称处理：保留左半部分，镜像替换右半部分"""
    try:
        with Image.open(image_path) as img:
            # 转换为支持的格式，但不强制RGBA
            img = convert_to_supported_mode(img)

            width, height = img.size
            # 创建新图像，使用与原图相同的模式
            result = Image.new(img.mode, (width, height))

            # 保留左半部分
            left_half = img.crop((0, 0, width // 2, height))
            result.paste(left_half, (0, 0))

            # 镜像左半部分作为右半部分
            mirrored_left = left_half.transpose(Image.FLIP_LEFT_RIGHT)
            result.paste(mirrored_left, (width // 2, 0))

            return result

    except Exception as e:
        logger.error(f"左对称处理错误: {e}")
        return None


async def process_right_symmetry(image_path: str) -> Image.Image:
    """右对称处理：保留右半部分，镜像替换左半部分"""
    try:
        with Image.open(image_path) as img:
            # 转换为支持的格式，但不强制RGBA
            img = convert_to_supported_mode(img)

            width, height = img.size
            # 创建新图像，使用与原图相同的模式
            result = Image.new(img.mode, (width, height))

            # 保留右半部分
            right_half = img.crop((width // 2, 0, width, height))
            result.paste(right_half, (width // 2, 0))

            # 镜像右半部分作为左半部分
            mirrored_right = right_half.transpose(Image.FLIP_LEFT_RIGHT)
            result.paste(mirrored_right, (0, 0))

            return result

    except Exception as e:
        logger.error(f"右对称处理错误: {e}")
        return None


async def process_center_symmetry(image_path: str) -> Image.Image:
    """中心对称处理：同时做左对称和上对称"""
    try:
        with Image.open(image_path) as img:
            # 转换为支持的格式，但不强制RGBA
            img = convert_to_supported_mode(img)

            width, height = img.size
            # 创建新图像，使用与原图相同的模式
            result = Image.new(img.mode, (width, height))

            # 取左上四分之一
            top_left = img.crop((0, 0, width // 2, height // 2))

            # 左上四分之一
            result.paste(top_left, (0, 0))

            # 右上四分之一（水平镜像）
            top_right = top_left.transpose(Image.FLIP_LEFT_RIGHT)
            result.paste(top_right, (width // 2, 0))

            # 左下四分之一（垂直镜像）
            bottom_left = top_left.transpose(Image.FLIP_TOP_BOTTOM)
            result.paste(bottom_left, (0, height // 2))

            # 右下四分之一（同时水平和垂直镜像）
            bottom_right = top_left.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_TOP_BOTTOM)
            result.paste(bottom_right, (width // 2, height // 2))

            return result

    except Exception as e:
        logger.error(f"中心对称处理错误: {e}")
        return None


async def process_top_symmetry(image_path: str) -> Image.Image:
    """上对称处理：保留上半部分，镜像替换下半部分"""
    try:
        with Image.open(image_path) as img:
            # 转换为支持的格式，但不强制RGBA
            img = convert_to_supported_mode(img)

            width, height = img.size
            # 创建新图像，使用与原图相同的模式
            result = Image.new(img.mode, (width, height))

            # 保留上半部分
            top_half = img.crop((0, 0, width, height // 2))
            result.paste(top_half, (0, 0))

            # 镜像上半部分作为下半部分
            mirrored_top = top_half.transpose(Image.FLIP_TOP_BOTTOM)
            result.paste(mirrored_top, (0, height // 2))

            return result

    except Exception as e:
        logger.error(f"上对称处理错误: {e}")
        return None


async def process_bottom_symmetry(image_path: str) -> Image.Image:
    """下对称处理：保留下半部分，镜像替换上半部分"""
    try:
        with Image.open(image_path) as img:
            # 转换为支持的格式，但不强制RGBA
            img = convert_to_supported_mode(img)

            width, height = img.size
            # 创建新图像，使用与原图相同的模式
            result = Image.new(img.mode, (width, height))

            # 保留下半部分
            bottom_half = img.crop((0, height // 2, width, height))
            result.paste(bottom_half, (0, height // 2))

            # 镜像下半部分作为上半部分
            mirrored_bottom = bottom_half.transpose(Image.FLIP_TOP_BOTTOM)
            result.paste(mirrored_bottom, (0, 0))

            return result

    except Exception as e:
        logger.error(f"下对称处理错误: {e}")
        return None


async def process_gif_symmetry(image_path: str, symmetry_type: str) -> str:
    """处理GIF对称 - 逐帧处理，改进透明度处理"""
    temp_frame_paths = []  # 记录所有临时文件路径
    processed_frame_paths = []  # 记录处理后的临时文件路径

    try:
        gif = Image.open(image_path)
        frames = []
        durations = []
        disposal_methods = []  # 记录每一帧的处置方法

        frame_index = 0
        for frame in ImageSequence.Iterator(gif):
            try:
                # 转换为支持的格式，正确处理透明度
                frame_converted = convert_to_supported_mode(frame)

                # 保存当前帧为临时文件
                temp_frame_path = os.path.join(tempfile.gettempdir(), f"temp_frame_{os.urandom(4).hex()}.png")
                frame_converted.save(temp_frame_path, 'PNG')
                temp_frame_paths.append(temp_frame_path)

                # 对当前帧进行对称处理
                if symmetry_type == "left":
                    processed_frame = await process_left_symmetry(temp_frame_path)
                elif symmetry_type == "right":
                    processed_frame = await process_right_symmetry(temp_frame_path)
                elif symmetry_type == "center":
                    processed_frame = await process_center_symmetry(temp_frame_path)
                elif symmetry_type == "top":
                    processed_frame = await process_top_symmetry(temp_frame_path)
                elif symmetry_type == "bottom":
                    processed_frame = await process_bottom_symmetry(temp_frame_path)
                else:  # 默认左对称
                    processed_frame = await process_left_symmetry(temp_frame_path)

                if processed_frame:
                    # 保存处理后的帧到临时文件
                    processed_frame_path = os.path.join(tempfile.gettempdir(),
                                                        f"processed_frame_{os.urandom(4).hex()}.png")

                    # 根据处理后的帧模式选择保存方式
                    if processed_frame.mode == 'RGBA':
                        processed_frame.save(processed_frame_path, 'PNG')
                    else:
                        processed_frame.save(processed_frame_path, 'PNG')

                    processed_frame_paths.append(processed_frame_path)

                    # 重新打开处理后的帧
                    processed_frame_img = Image.open(processed_frame_path)
                    frames.append(processed_frame_img)
                    durations.append(frame.info.get('duration', 100))

                    # 记录处置方法，如果有的话
                    disposal = frame.info.get('disposal', 2)
                    disposal_methods.append(disposal)
                else:
                    logger.error(f"第{frame_index}帧处理失败，跳过")

                frame_index += 1

            except Exception as frame_error:
                logger.error(f"处理第{frame_index}帧时出错: {frame_error}")
                continue

        if not frames:
            raise Exception("没有成功处理的帧")

        # 确定保存模式 - 如果有任何帧是RGBA模式，则整个GIF保存为RGBA
        save_mode = 'RGB'
        has_transparency = any(frame.mode == 'RGBA' for frame in frames)
        if has_transparency:
            save_mode = 'RGBA'
            logger.info("检测到透明帧，GIF将保存为RGBA模式")

        # 确保所有帧都是相同的模式
        unified_frames = []
        for frame in frames:
            if frame.mode != save_mode:
                if save_mode == 'RGB':
                    unified_frames.append(frame.convert('RGB'))
                else:
                    # 转换为RGBA时，确保透明度正确
                    if frame.mode == 'RGB':
                        # RGB转RGBA，设置完全不透明
                        rgba_frame = Image.new('RGBA', frame.size)
                        rgba_frame.paste(frame, (0, 0))
                        unified_frames.append(rgba_frame)
                    else:
                        unified_frames.append(frame.convert('RGBA'))
            else:
                unified_frames.append(frame)

        # 保存为新的GIF
        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_symmetry"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"symmetry_{symmetry_type}_{os.urandom(4).hex()}.gif"

        save_kwargs = {
            'save_all': True,
            'append_images': unified_frames[1:],
            'duration': durations,
            'loop': 0,
            # 'optimize': False, # 将在下面根据条件设置
        }

        # *** 【修复】 ***
        # 透明GIF的特殊处理
        if has_transparency:
            # disposal=2 指示渲染器在下一帧之前恢复到之前的状态（对透明GIF至关重要）
            save_kwargs['disposal'] = 2
            save_kwargs['optimize'] = False
            # 移除 'transparency' 关键字，PIL 会自动处理 RGBA 帧的 Alpha 通道
            logger.info("保存透明GIF，设置 disposal=2, optimize=False")
        else:
            # 对于不透明的RGB-GIF，可以安全地开启优化
            save_kwargs['optimize'] = True
            logger.info("保存不透明GIF，设置 optimize=True")

        # 保存第一帧
        first_frame = unified_frames[0]

        # 直接使用 (RGB或RGBA) 模式保存
        first_frame.save(str(output_path), **save_kwargs)

        logger.info(f"GIF对称处理完成 ({save_mode} 模式): {len(frames)} 帧, 保存到 {output_path}")

        return str(output_path)

    except Exception as e:
        logger.error(f"GIF对称处理错误: {e}")
        import traceback
        traceback.print_exc()
        return ""
    finally:
        # 确保清理所有临时文件
        all_temp_files = temp_frame_paths + processed_frame_paths
        for temp_path in all_temp_files:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception as delete_error:
                    logger.error(f"删除临时文件失败 {temp_path}: {delete_error}")


async def process_image_symmetry(image_url: str, symmetry_type: str) -> str:
    """主对称处理函数 - 支持静态图片和GIF"""
    image_path = None
    try:
        logger.info(f"开始处理对称图片: {symmetry_type}, URL: {image_url[:100]}...")

        # 下载图片
        image_path = await download_image(image_url)
        if not image_path or not os.path.exists(image_path):
            raise Exception("下载图片失败或文件不存在")

        file_size = os.path.getsize(image_path)
        logger.info(f"图片下载成功: {image_path}, 大小: {file_size} bytes")

        # 检查是否为GIF
        is_gif = False
        try:
            with Image.open(image_path) as img:
                if hasattr(img, 'is_animated') and img.is_animated:
                    is_gif = True
                    logger.info("检测到GIF图片，将进行逐帧处理")
        except Exception as img_error:
            logger.error(f"检查图片格式时出错: {img_error}")

        # 根据类型选择处理方法
        if is_gif:
            logger.info("开始GIF对称处理...")
            result_path = await process_gif_symmetry(image_path, symmetry_type)
        else:
            logger.info("开始静态图片对称处理...")
            # 静态图片处理
            if symmetry_type == "left":
                processed_image = await process_left_symmetry(image_path)
            elif symmetry_type == "right":
                processed_image = await process_right_symmetry(image_path)
            elif symmetry_type == "center":
                processed_image = await process_center_symmetry(image_path)
            elif symmetry_type == "top":
                processed_image = await process_top_symmetry(image_path)
            elif symmetry_type == "bottom":
                processed_image = await process_bottom_symmetry(image_path)
            else:  # 默认左对称
                processed_image = await process_left_symmetry(image_path)

            if processed_image:
                output_dir = Path(tempfile.gettempdir()) / "nonebot_image_symmetry"
                output_dir.mkdir(exist_ok=True)
                output_path = output_dir / f"symmetry_{symmetry_type}_{os.urandom(4).hex()}.png"

                # 根据图像模式选择保存格式
                if processed_image.mode == 'RGBA':
                    processed_image.save(output_path, 'PNG')
                else:
                    processed_image.save(output_path, 'PNG')  # PNG支持RGB和RGBA

                result_path = str(output_path)
                logger.info(f"静态图片对称处理完成: {result_path}")
            else:
                result_path = ""
                logger.error("静态图片对称处理失败")

        if result_path and os.path.exists(result_path):
            result_size = os.path.getsize(result_path)
            logger.info(f"对称处理成功: {result_path}, 大小: {result_size} bytes")
            return result_path
        else:
            logger.error("对称处理失败: 未生成有效输出文件")
            return ""

    except Exception as e:
        logger.error(f"对称处理过程出错: {e}")
        import traceback
        traceback.print_exc()
        return ""
    finally:
        # 清理下载的临时文件
        if image_path and os.path.exists(image_path):
            try:
                await safe_delete_file(image_path)
                logger.info(f"已清理临时文件: {image_path}")
            except Exception as delete_error:
                logger.error(f"清理临时文件失败: {delete_error}")