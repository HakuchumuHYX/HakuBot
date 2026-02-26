# image_mirror.py
import os
import tempfile
import aiohttp
from PIL import Image, ImageSequence
from pathlib import Path
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
        if 'transparency' in img.info:
            return img.convert('RGBA')
        else:
            return img.convert('RGB')
    elif img.mode == 'LA':
        return img.convert('RGBA')
    elif img.mode not in ['RGB', 'RGBA']:
        return img.convert('RGB')
    return img


def get_transpose_method(direction: str):
    """获取镜像翻转的方法"""
    # 简化：只区分水平和垂直
    if direction in ["top", "bottom", "vertical"]:
        return Image.FLIP_TOP_BOTTOM
    else:
        # 默认为水平镜像 (left, right, horizontal)
        return Image.FLIP_LEFT_RIGHT


async def process_static_mirror(image_path: str, direction: str) -> str:
    """处理静态图片镜像"""
    try:
        with Image.open(image_path) as img:
            img = convert_to_supported_mode(img)
            method = get_transpose_method(direction)
            processed_image = img.transpose(method)

            output_dir = Path(tempfile.gettempdir()) / "nonebot_image_mirror"
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / f"mirror_{direction}_{os.urandom(4).hex()}.png"

            processed_image.save(output_path, 'PNG')
            return str(output_path)
    except Exception as e:
        logger.error(f"静态图片镜像处理错误: {e}")
        return ""


async def process_gif_mirror(image_path: str, direction: str) -> str:
    """处理GIF镜像 - 逐帧处理"""
    try:
        gif = Image.open(image_path)
        frames = []
        durations = []
        method = get_transpose_method(direction)

        for frame in ImageSequence.Iterator(gif):
            frame_converted = convert_to_supported_mode(frame)
            processed_frame = frame_converted.transpose(method)
            frames.append(processed_frame)
            durations.append(frame.info.get('duration', 100))

        if not frames:
            raise Exception("没有成功处理的帧")

        save_mode = 'RGB'
        has_transparency = any(frame.mode == 'RGBA' for frame in frames)
        if has_transparency:
            save_mode = 'RGBA'

        unified_frames = []
        for frame in frames:
            if frame.mode != save_mode:
                if save_mode == 'RGBA':
                    if frame.mode == 'RGB':
                        rgba = Image.new('RGBA', frame.size)
                        rgba.paste(frame, (0, 0))
                        unified_frames.append(rgba)
                    else:
                        unified_frames.append(frame.convert('RGBA'))
                else:
                    unified_frames.append(frame.convert('RGB'))
            else:
                unified_frames.append(frame)

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_mirror"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"mirror_{direction}_{os.urandom(4).hex()}.gif"

        save_kwargs = {
            'save_all': True,
            'append_images': unified_frames[1:],
            'duration': durations,
            'loop': 0,
        }

        if has_transparency:
            save_kwargs['disposal'] = 2
            save_kwargs['optimize'] = False
        else:
            save_kwargs['optimize'] = True

        unified_frames[0].save(str(output_path), **save_kwargs)
        return str(output_path)

    except Exception as e:
        logger.error(f"GIF镜像处理错误: {e}")
        return ""


async def process_image_mirror(image_url: str, direction: str) -> str:
    """主镜像处理函数"""
    image_path = None
    try:
        logger.info(f"开始处理镜像图片: {direction}...")
        image_path = await download_image(image_url)
        if not image_path: raise Exception("下载失败")

        is_gif = False
        try:
            with Image.open(image_path) as img:
                if hasattr(img, 'is_animated') and img.is_animated:
                    is_gif = True
        except:
            pass

        if is_gif:
            result_path = await process_gif_mirror(image_path, direction)
        else:
            result_path = await process_static_mirror(image_path, direction)

        return result_path if result_path and os.path.exists(result_path) else ""

    except Exception as e:
        logger.error(f"镜像处理出错: {e}")
        return ""
    finally:
        if image_path and os.path.exists(image_path):
            try:
                await safe_delete_file(image_path)
            except:
                pass