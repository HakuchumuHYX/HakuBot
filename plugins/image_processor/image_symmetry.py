# image_symmetry.py
import os
import tempfile
import aiohttp
from PIL import Image, ImageSequence
from pathlib import Path
import numpy as np


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
    """将图像转换为支持的格式，但不强制RGBA"""
    if img.mode == 'P':
        # 调色板模式转换为RGB
        return img.convert('RGB')
    elif img.mode not in ['RGB', 'RGBA']:
        return img.convert('RGB')
    return img


async def process_left_symmetry(image_path: str) -> str:
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
        print(f"左对称处理错误: {e}")
        return None


async def process_right_symmetry(image_path: str) -> str:
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
        print(f"右对称处理错误: {e}")
        return None


async def process_center_symmetry(image_path: str) -> str:
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
        print(f"中心对称处理错误: {e}")
        return None


async def process_top_symmetry(image_path: str) -> str:
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
        print(f"上对称处理错误: {e}")
        return None


async def process_bottom_symmetry(image_path: str) -> str:
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
        print(f"下对称处理错误: {e}")
        return None


async def process_gif_symmetry(image_path: str, symmetry_type: str) -> str:
    """处理GIF对称 - 逐帧处理"""
    try:
        gif = Image.open(image_path)
        frames = []
        durations = []
        original_mode = gif.mode  # 保存原始模式

        for frame in ImageSequence.Iterator(gif):
            # 转换为支持的格式，但不强制RGBA
            frame_converted = convert_to_supported_mode(frame)

            # 保存当前帧为临时文件
            temp_frame_path = os.path.join(tempfile.gettempdir(), f"temp_frame_{os.urandom(4).hex()}.png")
            frame_converted.save(temp_frame_path, 'PNG')

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
                frames.append(processed_frame)
                durations.append(frame.info.get('duration', 100))

            # 清理临时文件
            if os.path.exists(temp_frame_path):
                await safe_delete_file(temp_frame_path)

        if not frames:
            raise Exception("没有成功处理的帧")

        # 确定保存模式
        save_mode = 'RGB'
        if any(frame.mode == 'RGBA' for frame in frames):
            save_mode = 'RGBA'

        # 确保所有帧都是相同的模式
        unified_frames = []
        for frame in frames:
            if frame.mode != save_mode:
                if save_mode == 'RGB':
                    unified_frames.append(frame.convert('RGB'))
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
            'optimize': True,
        }

        # 只有RGBA模式才需要设置disposal
        if save_mode == 'RGBA':
            save_kwargs['disposal'] = 2
            save_kwargs['transparency'] = 0

        unified_frames[0].save(str(output_path), **save_kwargs)

        return str(output_path)

    except Exception as e:
        print(f"GIF对称处理错误: {e}")
        return ""


async def process_image_symmetry(image_url: str, symmetry_type: str) -> str:
    """主对称处理函数 - 支持静态图片和GIF"""
    try:
        # 下载图片
        image_path = await download_image(image_url)

        # 检查是否为GIF
        is_gif = False
        try:
            with Image.open(image_path) as img:
                if hasattr(img, 'is_animated') and img.is_animated:
                    is_gif = True
        except:
            pass

        # 根据类型选择处理方法
        if is_gif:
            result_path = await process_gif_symmetry(image_path, symmetry_type)
        else:
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
            else:
                result_path = ""

        # 清理临时文件
        if os.path.exists(image_path):
            await safe_delete_file(image_path)

        return result_path if result_path and os.path.exists(result_path) else ""

    except Exception as e:
        print(f"对称处理错误: {e}")
        if 'image_path' in locals() and os.path.exists(image_path):
            await safe_delete_file(image_path)
        return ""