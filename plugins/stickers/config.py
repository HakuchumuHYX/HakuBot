# stickers/config.py - 配置管理
"""
Stickers 插件配置文件
集中管理所有可配置参数
"""

from typing import Set

from core.settings import ConfigModel, load_config

# ==================== 图片文件配置 ====================

# 支持的图片扩展名
IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


# ==================== 投稿配置 ====================

# 图片下载并发数
DOWNLOAD_CONCURRENCY: int = 5


# ==================== 随机获取配置 ====================

# 单次随机获取的最大图片数
MAX_RANDOM_COUNT: int = 5


# ==================== 预览图配置 ====================

# 预览图每行最多显示的文件夹数
PREVIEW_MAX_COLS: int = 7

# 预览图单元格宽度
PREVIEW_CELL_WIDTH: int = 220

# 预览图基础单元格高度
PREVIEW_BASE_CELL_HEIGHT: int = 190


# ==================== 概览图配置 ====================

# 概览图分批大小
OVERVIEW_BATCH_SIZE: int = 2000

# 概览图最大画布像素数 (50MP)
MAX_CANVAS_PIXELS: int = 50 * 1024 * 1024


# ==================== 本地配置（不进 git） ====================


class LocalConfig(ConfigModel):
    # 表情包下载站的网址，「查看stickers」发预览图时附在后面；没配就不附
    site_url: str


SITE_URL: str = load_config("stickers", LocalConfig).site_url
