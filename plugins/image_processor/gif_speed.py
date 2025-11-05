# gif_speed.py
import os
import tempfile
import aiohttp
from PIL import Image, ImageSequence
from pathlib import Path


async def download_file(url: str) -> str:
    """下载文件到临时目录"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
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


async def change_gif_speed(image_url: str, speed_factor: float) -> str:
    """改变GIF速度并返回处理后的文件路径"""
    temp_path = None
    try:
        # 限制最大倍速为5
        speed_factor = min(float(speed_factor), 5.0)

        # 下载GIF文件
        temp_path = await download_file(image_url)

        with Image.open(temp_path) as img:
            frames = []
            durations = []
            first_frame_size = None
            original_mode = img.mode  # 保存原始模式

            # 提取所有帧
            for frame in ImageSequence.Iterator(img):
                try:
                    frame_copy = frame.copy()

                    # 保持原始模式，不强制转换为RGBA
                    # 只在需要时转换模式
                    if frame_copy.mode == 'P':
                        # 如果是调色板模式，转换为RGB保持颜色
                        frame_copy = frame_copy.convert('RGB')
                    elif frame_copy.mode not in ['RGB', 'RGBA']:
                        frame_copy = frame_copy.convert('RGB')

                    if first_frame_size is None:
                        first_frame_size = frame_copy.size

                    if frame_copy.size != first_frame_size:
                        frame_copy = frame_copy.resize(first_frame_size, Image.Resampling.LANCZOS)

                    frames.append(frame_copy)
                    duration = frame.info.get('duration', 100)
                    durations.append(duration)

                except Exception as frame_error:
                    print(f"处理帧时出错: {frame_error}")
                    continue

            if not frames:
                raise Exception("没有成功提取到任何帧")

            # 计算新的持续时间（加速）
            # 确保持续时间不会太小，最小为20ms
            new_durations = [max(int(d / speed_factor), 20) for d in durations]

            # 创建输出目录
            output_dir = Path(tempfile.gettempdir()) / "nonebot_gif_speed"
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / f"speed_{speed_factor}x_{os.urandom(4).hex()}.gif"

            # 保存加速后的GIF
            first_frame = frames[0]

            # 确定保存模式
            save_mode = 'RGB'
            if any(frame.mode == 'RGBA' for frame in frames):
                save_mode = 'RGBA'

            # 如果所有帧都是RGB模式，确保保存为RGB
            if save_mode == 'RGB':
                # 确保所有帧都是RGB模式
                rgb_frames = []
                for frame in frames:
                    if frame.mode != 'RGB':
                        rgb_frames.append(frame.convert('RGB'))
                    else:
                        rgb_frames.append(frame)
                frames = rgb_frames

            # 保存GIF
            save_kwargs = {
                'save_all': True,
                'append_images': frames[1:],
                'duration': new_durations,
                'loop': 0,
                'optimize': True,
            }

            # 只有RGBA模式才需要设置disposal
            if save_mode == 'RGBA':
                save_kwargs['disposal'] = 2

            first_frame.save(str(output_path), **save_kwargs)

            print(f"GIF倍速处理成功: {len(frames)} 帧, 加速 {speed_factor} 倍")
            print(f"原持续时间: {durations[:5]}...")  # 打印前5个持续时间用于调试
            print(f"新持续时间: {new_durations[:5]}...")

        # 清理临时文件
        if temp_path:
            await safe_delete_file(temp_path)

        return str(output_path)

    except Exception as e:
        print(f"GIF倍速处理错误: {e}")
        if temp_path:
            await safe_delete_file(temp_path)
        return ""


def change_gif_speed_alternative(image_url: str, speed_factor: float) -> str:
    """备选方案：使用imageio处理GIF倍速"""
    try:
        import imageio
        import requests
        from io import BytesIO

        speed_factor = min(float(speed_factor), 5.0)

        response = requests.get(image_url)
        response.raise_for_status()

        gif_reader = imageio.get_reader(BytesIO(response.content))
        frames = []
        durations = []

        for frame_data in gif_reader:
            frame = Image.fromarray(frame_data)
            # 保持原始颜色模式
            if frame.mode == 'P':
                frame = frame.convert('RGB')
            frames.append(frame)

            # 直接从元数据获取持续时间
            meta = gif_reader.get_meta_data()
            # 尝试多种方式获取持续时间
            duration = meta.get('duration', 100)  # 秒为单位
            if 'fps' in meta:
                duration = 1000 / meta['fps']  # 转换为毫秒
            elif hasattr(gif_reader, '_meta') and 'duration' in gif_reader._meta:
                duration = gif_reader._meta['duration'] * 1000  # 转换为毫秒
            else:
                duration = 100  # 默认值

            durations.append(int(duration))

        # 统一帧尺寸
        if frames:
            target_size = frames[0].size
            for i in range(len(frames)):
                if frames[i].size != target_size:
                    frames[i] = frames[i].resize(target_size, Image.Resampling.LANCZOS)

        # 计算新的持续时间
        new_durations = [max(int(d / speed_factor), 20) for d in durations]

        # 保存
        output_dir = Path(tempfile.gettempdir()) / "nonebot_gif_speed"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"speed_alt_{speed_factor}x_{os.urandom(4).hex()}.gif"

        # 转换为numpy数组供imageio使用
        frame_arrays = []
        for frame in frames:
            if frame.mode == 'RGBA':
                # 对于RGBA图像，确保透明度正确处理
                import numpy as np
                frame_array = np.array(frame)
                frame_arrays.append(frame_array)
            else:
                frame_arrays.append(imageio.core.util.Array(frame))

        imageio.mimsave(
            str(output_path),
            frame_arrays,
            duration=[d / 1000 for d in new_durations],  # 转换为秒
            format='GIF'
        )

        return str(output_path)

    except Exception as e:
        print(f"备选方案GIF倍速错误: {e}")
        return ""
