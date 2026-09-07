"""猜卡面的随机裁剪逻辑。"""

import random
from PIL import Image


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
