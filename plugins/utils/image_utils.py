import base64
import mimetypes
from pathlib import Path
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger


def path_to_base64_image(path) -> MessageSegment:
    """
    将本地文件路径转换为 base64 编码的 MessageSegment.image，
    解决 NapCat 在 Docker 容器中无法访问宿主机本地路径的问题。

    Args:
        path: 文件路径，可以是 str 或 Path 对象

    Returns:
        MessageSegment.image (base64 编码)

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 文件为空
    """
    p = Path(path) if not isinstance(path, Path) else path

    if not p.exists():
        raise FileNotFoundError(f"图片文件不存在: {p}")

    if p.stat().st_size == 0:
        raise ValueError(f"图片文件为空: {p}")

    data = p.read_bytes()

    mime, _ = mimetypes.guess_type(str(p))
    if not mime:
        suffix = p.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
        }
        mime = mime_map.get(suffix, "image/png")

    b64 = base64.b64encode(data).decode("utf-8")
    return MessageSegment.image(f"base64://{b64}")


def path_to_base64_record(path) -> MessageSegment:
    """
    将本地音频文件路径转换为 base64 编码的 MessageSegment.record，
    解决 NapCat 在 Docker 容器中无法访问宿主机本地路径的问题。

    Args:
        path: 文件路径，可以是 str 或 Path 对象

    Returns:
        MessageSegment.record (base64 编码)
    """
    p = Path(path) if not isinstance(path, Path) else path

    if not p.exists():
        raise FileNotFoundError(f"音频文件不存在: {p}")

    if p.stat().st_size == 0:
        raise ValueError(f"音频文件为空: {p}")

    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("utf-8")
    return MessageSegment.record(f"base64://{b64}")
