# stickers/config.py - 配置管理
"""
Stickers 插件配置文件
集中管理所有可配置参数
"""

from typing import Set

# ==================== 图片文件配置 ====================

# 支持的图片扩展名
IMAGE_EXTENSIONS: Set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}


# ==================== 哈希算法配置 ====================

# dHash 尺寸 (产生 DHASH_SIZE * DHASH_SIZE 位哈希)
DHASH_SIZE: int = 8

# 汉明距离阈值，<= 此值认为图片相似
HAMMING_THRESHOLD: int = 5

# MSE 验证阈值（使用 numpy 时）
MSE_THRESHOLD_NUMPY: float = 100.0

# 平均差异阈值（纯 Python 时）
AVG_DIFF_THRESHOLD_PYTHON: float = 150.0


# ==================== 缓存配置 ====================

# 缓存版本号 - 算法变更时需更新此版本
CACHE_VERSION: str = "3.0"

# 缓存有效期（秒），30天
CACHE_TTL: int = 30 * 24 * 60 * 60


# ==================== 并行处理配置 ====================

# 每批并行处理的图片数量
BATCH_SIZE: int = 50

# 最大工作线程数（预留）
MAX_WORKERS: int = 8

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
