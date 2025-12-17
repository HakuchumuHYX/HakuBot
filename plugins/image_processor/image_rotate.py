# image_rotate.py
import os
import tempfile
import aiohttp
import math
from PIL import Image, ImageSequence
from pathlib import Path
from nonebot.log import logger


async def download_image(url: str) -> str:
    """下载图片到临时目录"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                temp_dir = tempfile.gettempdir()
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


def make_square_canvas(img: Image.Image) -> Image.Image:
    """
    将图片放置在一个足够大的正方形透明画布中心
    画布边长 = 原图对角线长度，防止旋转时被裁剪
    """
    width, height = img.size
    # 计算对角线长度
    diagonal = int(math.sqrt(width ** 2 + height ** 2)) + 2

    # 创建正方形透明画布
    canvas = Image.new('RGBA', (diagonal, diagonal), (0, 0, 0, 0))

    # 将原图粘贴到中心
    offset_x = (diagonal - width) // 2
    offset_y = (diagonal - height) // 2
    canvas.paste(img, (offset_x, offset_y))

    return canvas


async def process_rotate(image_path: str, direction: str, speed: float) -> str:
    """
    处理图片旋转生成GIF
    :param direction: 'clockwise' (顺时针) or 'counter_clockwise' (逆时针)
    :param speed: 转速倍率 (0.1 - 5.0)
    """
    try:
        # 限制转速范围
        speed = max(0.1, min(float(speed), 5.0))

        # 算法：
        # 基准(1.0x): 转一圈 2.0秒。帧间隔 50ms (20FPS)。
        # 总帧数 N = 2.0 / speed * 20 = 40 / speed
        # 步进角度 step = 360 / N

        frames_per_circle = int(40 / speed)
        # 限制最小帧数，防止步进过大看起来像闪烁
        frames_per_circle = max(4, frames_per_circle)
        # 限制最大帧数，防止生成文件过大
        frames_per_circle = min(200, frames_per_circle)

        step_angle = 360.0 / frames_per_circle

        if direction == 'clockwise':
            step_angle = -step_angle  # 顺时针是负角度

        logger.info(f"旋转参数: 倍速{speed}, 总帧数{frames_per_circle}, 步进{step_angle:.2f}度")

        input_img = Image.open(image_path)

        # 检查是否为 GIF 且有多帧
        is_animated = getattr(input_img, 'is_animated', False)
        original_frames = []

        if is_animated:
            # 读取所有原帧
            for frame in ImageSequence.Iterator(input_img):
                # 统一转换为 RGBA
                frame = frame.convert('RGBA')
                original_frames.append(frame)
        else:
            # 静态图转为单帧列表
            original_frames.append(input_img.convert('RGBA'))

        output_frames = []
        durations = []

        # 预先计算画布大小（基于第一帧），确保所有帧一致
        base_frame = original_frames[0]
        # 获取扩展后的正方形画布（作为底板）
        # 注意：这里我们每一帧都动态生成画布，因为如果是动图，每一帧内容不同
        # 但画布尺寸必须固定
        w, h = base_frame.size
        diagonal = int(math.sqrt(w ** 2 + h ** 2)) + 2
        canvas_size = (diagonal, diagonal)

        # 生成旋转帧
        num_original_frames = len(original_frames)

        for i in range(frames_per_circle):
            # 计算当前角度
            current_angle = i * step_angle

            # 获取当前内容帧（如果是动图，循环取原帧；如果是静态图，总是取第0帧）
            source_frame = original_frames[i % num_original_frames]

            # 1. 创建透明画布
            frame_canvas = Image.new('RGBA', canvas_size, (0, 0, 0, 0))

            # 2. 将源图粘贴到中心
            src_w, src_h = source_frame.size
            offset_x = (diagonal - src_w) // 2
            offset_y = (diagonal - src_h) // 2

            # 必须用 mask 粘贴以保持透明度
            frame_canvas.paste(source_frame, (offset_x, offset_y), source_frame)

            # 3. 旋转画布 (resample=Image.BICUBIC 质量较好)
            # center=None 默认围绕中心旋转
            rotated_frame = frame_canvas.rotate(current_angle, resample=Image.Resampling.BICUBIC)

            output_frames.append(rotated_frame)
            durations.append(50)  # 固定 50ms 帧间隔 (20fps)

        # 保存结果
        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_rotate"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"rotate_{direction}_{speed}x_{os.urandom(4).hex()}.gif"

        # 优化：如果原图是 RGBA，这里已经是 RGBA。
        # 设置 disposal=2 对透明旋转 GIF 很重要
        output_frames[0].save(
            str(output_path),
            save_all=True,
            append_images=output_frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
            optimize=False  # 关闭优化以防止透明度错误
        )

        return str(output_path)

    except Exception as e:
        logger.error(f"旋转处理错误: {e}")
        import traceback
        traceback.print_exc()
        return ""


async def process_image_rotate(image_url: str, direction: str, speed: float) -> str:
    """主旋转处理函数"""
    image_path = None
    try:
        # 下载图片
        image_path = await download_image(image_url)
        if not image_path:
            raise Exception("下载失败")

        result_path = await process_rotate(image_path, direction, speed)

        if result_path and os.path.exists(result_path):
            size = os.path.getsize(result_path)
            logger.info(f"旋转成功: {result_path}, 大小: {size}")
            return result_path
        else:
            return ""

    except Exception as e:
        logger.error(f"主旋转函数出错: {e}")
        return ""
    finally:
        if image_path and os.path.exists(image_path):
            try:
                await safe_delete_file(image_path)
            except:
                pass