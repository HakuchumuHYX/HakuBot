import os
import tempfile
import aiohttp
from PIL import Image, ImageSequence
from pathlib import Path
import shutil
from nonebot.log import logger

async def download_file(url: str) -> str:
    """下载文件到临时目录"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                # 创建临时文件
                temp_dir = tempfile.gettempdir()
                file_ext = '.gif'
                temp_path = os.path.join(temp_dir, f"temp_gif_{os.urandom(4).hex()}{file_ext}")

                content = await response.read()
                with open(temp_path, 'wb') as f:
                    f.write(content)

                return temp_path
            else:
                raise Exception(f"下载文件失败: {response.status}")


async def safe_delete_file(file_path: str, max_retries: int = 3):
    """安全删除文件，处理文件占用问题"""
    for i in range(max_retries):
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                return True
        except PermissionError as e:
            if i < max_retries - 1:
                import time
                time.sleep(0.1)  # 等待100ms后重试
            else:
                logger.error(f"无法删除文件 {file_path}: {e}")
                return False
    return False


async def reverse_gif(image_url: str) -> str:
    """反转GIF并返回处理后的文件路径"""
    temp_path = None
    try:
        # 下载GIF文件
        temp_path = await download_file(image_url)

        # 使用PIL处理GIF
        with Image.open(temp_path) as img:
            frames = []
            durations = []

            # 获取第一帧的尺寸作为参考
            first_frame_size = None

            # 遍历每一帧
            for frame in ImageSequence.Iterator(img):
                try:
                    # 复制当前帧
                    frame_copy = frame.copy()

                    # 确保所有帧都是RGBA模式（支持透明度）
                    if frame_copy.mode != 'RGBA':
                        frame_copy = frame_copy.convert('RGBA')

                    # 记录第一帧尺寸
                    if first_frame_size is None:
                        first_frame_size = frame_copy.size

                    # 确保所有帧尺寸一致
                    if frame_copy.size != first_frame_size:
                        frame_copy = frame_copy.resize(first_frame_size, Image.Resampling.LANCZOS)

                    frames.append(frame_copy)

                    # 获取帧持续时间
                    duration = frame.info.get('duration', 100)
                    durations.append(duration)

                except Exception as frame_error:
                    logger.error(f"处理帧时出错: {frame_error}")
                    continue

            if not frames:
                raise Exception("没有成功提取到任何帧")

            # 反转帧序列和持续时间
            reversed_frames = frames[::-1]
            reversed_durations = durations[::-1]

            # 创建输出目录
            output_dir = Path(tempfile.gettempdir()) / "nonebot_gif_reverse"
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / f"reversed_{os.urandom(4).hex()}.gif"

            # 保存反转后的GIF
            # 使用第一帧作为基础
            first_frame = reversed_frames[0]

            # 保存GIF，设置优化选项
            first_frame.save(
                str(output_path),
                save_all=True,
                append_images=reversed_frames[1:],
                duration=reversed_durations,
                loop=0,  # 无限循环
                disposal=2,  # 恢复背景
                optimize=True,
                format='GIF'
            )

            logger.info(f"GIF倒放成功: {len(frames)} 帧")

        # 清理临时文件
        if temp_path:
            await safe_delete_file(temp_path)

        return str(output_path)

    except Exception as e:
        logger.error(f"GIF倒放错误: {e}")
        # 清理临时文件
        if temp_path:
            await safe_delete_file(temp_path)
        return ""


def reverse_gif_alternative(image_url: str) -> str:
    """备选方案：使用imageio处理GIF"""
    try:
        import imageio
        import requests
        from io import BytesIO

        # 下载图片
        response = requests.get(image_url)
        response.raise_for_status()

        # 使用imageio读取GIF
        gif_reader = imageio.get_reader(BytesIO(response.content))

        frames = []
        durations = []

        for frame_data in gif_reader:
            # 转换为PIL图像进行尺寸统一
            frame = Image.fromarray(frame_data)
            if frame.mode != 'RGBA':
                frame = frame.convert('RGBA')
            frames.append(frame)

            # 获取元数据中的帧率并转换为持续时间
            meta = gif_reader.get_meta_data()
            fps = meta.get('fps', 10)
            duration = int(1000 / fps)  # 转换为毫秒
            durations.append(duration)

        # 统一帧尺寸
        if frames:
            target_size = frames[0].size
            for i in range(len(frames)):
                if frames[i].size != target_size:
                    frames[i] = frames[i].resize(target_size, Image.Resampling.LANCZOS)

        # 反转
        reversed_frames = frames[::-1]

        # 保存
        output_dir = Path(tempfile.gettempdir()) / "nonebot_gif_reverse"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"reversed_alt_{os.urandom(4).hex()}.gif"

        # 转换为numpy数组供imageio使用
        frame_arrays = [imageio.core.util.Array(frame) for frame in reversed_frames]

        imageio.mimsave(
            str(output_path),
            frame_arrays,
            duration=[d / 1000 for d in durations],  # 转换为秒
            format='GIF'
        )

        return str(output_path)

    except Exception as e:
        logger.error(f"备选方案GIF倒放错误: {e}")
        return ""