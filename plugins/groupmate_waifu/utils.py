"""将公共头像下载结果转换为本插件使用的 PNG。"""

import io
from pil_utils import BuildImage
from utils.onebot.avatar import download_avatar


async def download_user_img(user_id: int) -> bytes:
    """
    下载用户头像并转换为 PNG 格式

    Args:
        user_id: 用户 QQ 号

    Returns:
        PNG 格式的头像字节内容
    """
    data = await download_avatar(user_id, size=640)
    img = BuildImage.open(io.BytesIO(data))
    return img.save_png()

