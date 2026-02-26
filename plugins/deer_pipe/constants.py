"""deer_pipe 插件常量定义模块"""

from pathlib import Path
from typing import TYPE_CHECKING

import nonebot_plugin_localstore as localstore
from nonebot import logger

if TYPE_CHECKING:
    from PIL.ImageFile import ImageFile
    from PIL.ImageFont import FreeTypeFont


# 插件信息
PLUGIN_PATH: Path = Path(__file__).parent.resolve()
PLUGIN_VERSION: str = "1.0.1"
PLUGIN_ID: str = "deer_pipe"

# 资源路径
ASSETS_PATH: Path = PLUGIN_PATH / "assets"
FONT_PATH: Path = ASSETS_PATH / "MiSans-Regular.ttf"
CHECK_IMAGE_PATH: Path = ASSETS_PATH / "check@96x100.png"
DEERPIPE_IMAGE_PATH: Path = ASSETS_PATH / "deerpipe@100x82.png"

# 数据库配置
DATABASE_VERSION: int = 3  # 升级版本号以支持年份字段
DATABASE_NAME: str = f"userdata-v{DATABASE_VERSION}.db"
DATABASE_PATH: Path = localstore.get_plugin_data_file(DATABASE_NAME)
DATABASE_URL: str = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# 日历图片尺寸常量
CALENDAR_BOX_WIDTH: int = 100
CALENDAR_BOX_HEIGHT: int = 100
CALENDAR_IMAGE_WIDTH: int = 700
FONT_SIZE: int = 25


# 延迟加载资源的辅助函数
_font_cache: "FreeTypeFont | None" = None
_check_image_cache: "ImageFile | None" = None
_deerpipe_image_cache: "ImageFile | None" = None


def get_font() -> "FreeTypeFont":
    """获取字体对象（延迟加载）"""
    global _font_cache
    if _font_cache is None:
        from PIL import ImageFont
        logger.debug("加载字体资源: MiSans-Regular.ttf")
        _font_cache = ImageFont.truetype(str(FONT_PATH), FONT_SIZE)
    return _font_cache


def get_check_image() -> "ImageFile":
    """获取签到勾选图片（延迟加载）"""
    global _check_image_cache
    if _check_image_cache is None:
        from PIL import Image
        logger.debug("加载图片资源: check@96x100.png")
        _check_image_cache = Image.open(CHECK_IMAGE_PATH).convert("RGBA")
    return _check_image_cache


def get_deerpipe_image() -> "ImageFile":
    """获取鹿管图片（延迟加载）"""
    global _deerpipe_image_cache
    if _deerpipe_image_cache is None:
        from PIL import Image
        logger.debug("加载图片资源: deerpipe@100x82.png")
        _deerpipe_image_cache = Image.open(DEERPIPE_IMAGE_PATH).convert("RGBA")
    return _deerpipe_image_cache
