# image_cutout.py
import os

# 强制使用CPU，避免CUDA依赖问题
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'  # 禁用GPU
os.environ['ORT_DISABLE_CUDA'] = '1'  # 禁用ONNX Runtime的CUDA

import tempfile
import aiohttp
from PIL import Image
from pathlib import Path
import numpy as np
import cv2
from nonebot.log import logger


async def download_image(url: str) -> str:
    """下载图片到临时目录"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                temp_dir = tempfile.gettempdir()
                file_ext = '.png'
                temp_path = os.path.join(temp_dir, f"temp_img_{os.urandom(4).hex()}{file_ext}")

                content = await response.read()
                with open(temp_path, 'wb') as f:
                    f.write(content)

                return temp_path
            else:
                raise Exception(f"下载图片失败: {response.status}")


async def remove_background_rembg(image_path: str) -> str:
    """使用rembg进行高质量背景移除"""
    try:
        # 在导入rembg之前确保环境变量已设置
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        os.environ['ORT_DISABLE_CUDA'] = '1'

        from rembg import remove
        from rembg.session_factory import new_session

        # 使用u2net模型，但强制使用CPU
        session = new_session("u2net", providers=['CPUExecutionProvider'])

        with open(image_path, 'rb') as input_file:
            input_data = input_file.read()

        # 移除背景
        output_data = remove(input_data, session=session)

        # 保存结果
        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_rembg_{os.urandom(4).hex()}.png"

        with open(output_path, 'wb') as output_file:
            output_file.write(output_data)

        return str(output_path)

    except ImportError:
        logger.error("rembg未安装，请安装: pip install rembg")
        return await remove_background_opencv(image_path)
    except Exception as e:
        logger.error(f"rembg抠图错误: {e}")
        return await remove_background_opencv(image_path)


# 其余代码保持不变...
async def remove_background_opencv(image_path: str) -> str:
    """使用OpenCV进行精确的背景移除"""
    try:
        import cv2
        import numpy as np

        # 读取图像
        img = cv2.imread(image_path)
        if img is None:
            raise Exception("无法读取图像")

        # 转换为RGB
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # 创建掩码
        mask = np.zeros(img.shape[:2], np.uint8)

        # 定义前景和背景模型
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)

        # 定义矩形ROI (x, y, width, height)，可以根据需要调整
        height, width = img.shape[:2]
        rect = (int(width * 0.1), int(height * 0.1), int(width * 0.8), int(height * 0.8))

        # 应用GrabCut算法
        cv2.grabCut(img_rgb, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)

        # 创建前景掩码
        mask2 = np.where((mask == 2) | (mask == 0), 0, 1).astype('uint8')

        # 应用掩码
        result = img_rgb * mask2[:, :, np.newaxis]

        # 创建透明背景
        rgba = cv2.cvtColor(result, cv2.COLOR_RGB2RGBA)
        rgba[:, :, 3] = mask2 * 255

        # 保存结果
        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_cv_{os.urandom(4).hex()}.png"

        cv2.imwrite(str(output_path), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))

        return str(output_path)

    except Exception as e:
        logger.error(f"OpenCV抠图错误: {e}")
        return await remove_background_simple(image_path)


async def remove_background_simple(image_path: str) -> str:
    """改进的简单背景移除"""
    try:
        img = Image.open(image_path).convert('RGBA')
        img_array = np.array(img)

        # 更精确的背景检测
        r, g, b, a = img_array[:, :, 0], img_array[:, :, 1], img_array[:, :, 2], img_array[:, :, 3]

        # 检测多种背景颜色（白色、浅色等）
        white_threshold = 230
        light_threshold = 240

        # 白色背景检测
        white_mask = (r > white_threshold) & (g > white_threshold) & (b > white_threshold)

        # 浅色背景检测
        light_mask = (r > light_threshold) | (g > light_threshold) | (b > light_threshold)

        # 边缘检测辅助
        gray = cv2.cvtColor(img_array[:, :, :3], cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_mask = edges > 0

        # 合并掩码：保留边缘区域，移除纯色背景
        background_mask = (white_mask | light_mask) & ~edge_mask

        # 设置背景为透明
        img_array[background_mask] = [0, 0, 0, 0]

        result_img = Image.fromarray(img_array, 'RGBA')

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_simple_{os.urandom(4).hex()}.png"

        result_img.save(output_path, 'PNG')

        return str(output_path)

    except Exception as e:
        logger.error(f"简单抠图错误: {e}")
        return ""


async def remove_background_gif(image_path: str) -> str:
    """处理GIF抠图 - 逐帧处理"""
    try:
        from PIL import Image, ImageSequence

        # 读取GIF
        gif = Image.open(image_path)
        frames = []
        durations = []

        # 处理每一帧
        for frame in ImageSequence.Iterator(gif):
            # 转换为RGBA
            frame_rgba = frame.convert('RGBA')

            # 保存当前帧为临时文件
            temp_frame_path = os.path.join(tempfile.gettempdir(), f"temp_frame_{os.urandom(4).hex()}.png")
            frame_rgba.save(temp_frame_path, 'PNG')

            # 对当前帧进行抠图
            processed_frame_path = await remove_background_rembg(temp_frame_path)
            if processed_frame_path and os.path.exists(processed_frame_path):
                processed_frame = Image.open(processed_frame_path).convert('RGBA')
                frames.append(processed_frame)
                durations.append(frame.info.get('duration', 100))

            # 清理临时文件
            if os.path.exists(temp_frame_path):
                os.unlink(temp_frame_path)
            if os.path.exists(processed_frame_path):
                os.unlink(processed_frame_path)

        if not frames:
            raise Exception("没有成功处理的帧")

        # 保存为新的GIF
        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_gif_{os.urandom(4).hex()}.gif"

        frames[0].save(
            str(output_path),
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            format='GIF',
            disposal=2,  # 恢复背景色
            transparency=0
        )

        return str(output_path)

    except Exception as e:
        logger.error(f"GIF抠图错误: {e}")
        return await remove_background_rembg(image_path)


async def remove_background(image_url: str) -> str:
    """主抠图函数 - 支持静态图片和GIF"""
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
            result_path = await remove_background_gif(image_path)
        else:
            # 优先使用rembg，失败时降级
            result_path = await remove_background_rembg(image_path)

        # 清理临时文件
        if os.path.exists(image_path):
            os.unlink(image_path)

        return result_path if result_path and os.path.exists(result_path) else ""

    except Exception as e:
        logger.error(f"抠图处理错误: {e}")
        if 'image_path' in locals() and os.path.exists(image_path):
            os.unlink(image_path)
        return ""