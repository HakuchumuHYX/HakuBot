import base64
from pathlib import Path
from typing import Tuple
from nonebot import logger

def image_to_base64(image_path: Path) -> Tuple[bool, str]:
    """将本地图片转换为base64编码"""
    try:
        if not image_path.exists():
            return False, f"图片文件不存在: {image_path}"
        if image_path.stat().st_size > 10 * 1024 * 1024:  # 10MB限制
            return False, f"图片文件过大: {image_path.stat().st_size / 1024 / 1024:.2f}MB"

        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')

        return True, f"base64://{base64_data}"
    except Exception as e:
        logger.error(f"图片转base64失败: {e}")
        return False, f"图片转换失败: {str(e)}"
