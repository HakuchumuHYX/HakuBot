"""图片下载与处理模块"""
import random
import io
from PIL import Image
import httpx


async def download_image(url: str, timeout: int = 15) -> Image.Image:
    """下载图片并返回 PIL Image 对象"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")


def random_crop_image(
    image: Image.Image,
    rate_min: float = 0.15,
    rate_max: float = 0.25,
) -> Image.Image:
    """
    随机裁剪图片的一小块区域
    rate_min/rate_max: 裁剪区域占原图宽高的比例范围
    """
    w, h = image.size
    w_rate = random.uniform(rate_min, rate_max)
    h_rate = random.uniform(rate_min, rate_max)
    w_crop = int(w * w_rate)
    h_crop = int(h * h_rate)
    x = random.randint(0, w - w_crop)
    y = random.randint(0, h - h_crop)
    return image.crop((x, y, x + w_crop, y + h_crop))


def image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    """将 PIL Image 转为 bytes"""
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()
