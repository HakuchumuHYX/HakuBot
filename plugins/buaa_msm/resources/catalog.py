# plugins/buaa_msm/resources/catalog.py
"""
公共资源目录（catalog）

目的：
- 承载 buaa_msm 内部“既被当前渲染链路使用、也可能被 legacy 渲染器使用”的资源定义与加载缓存：
  - SCENES（地图底图与坐标换算参数）
  - ITEM_TEXTURES（资源 id -> icon path）
  - RARE_ITEM / SUPER_RARE_ITEM（稀有度定义）
  - get_font / get_icon（字体与图标的缓存加载）

说明：
- 该模块不应包含 NoneBot 命令/定时任务注册。
- 后续 `paint.py` 将降级为兼容壳：仅 re-export 这些符号，避免旧 import 断裂。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from PIL import Image, ImageFont
from nonebot.log import logger

from ..config import plugin_config, SCENE_KEY_TO_NAME

# 从配置获取路径
resource_dir: Path = plugin_config.resource_dir
output_dir: Path = plugin_config.output_dir

# ============== 场景参数（地图底图 + 坐标换算） ==============

SCENES = {
    "scene1": {
        "physicalWidth": 33.333,
        "offsetX": 0,
        "offsetY": -40,
        "imagePath": str(resource_dir / "img" / "grassland.png"),
        "xDirection": "x-",
        "yDirection": "y-",
        "reverseXY": True,
    },
    "scene2": {
        "physicalWidth": 24.806,
        "offsetX": -62.015,
        "offsetY": 20.672,
        "imagePath": str(resource_dir / "img" / "flowergarden.png"),
        "xDirection": "x-",
        "yDirection": "y-",
        "reverseXY": True,
    },
    "scene3": {
        "physicalWidth": 20.513,
        "offsetX": 0,
        "offsetY": 80,
        "imagePath": str(resource_dir / "img" / "beach.png"),
        "xDirection": "x+",
        "yDirection": "y-",
        "reverseXY": False,
    },
    "scene4": {
        "physicalWidth": 21.333,
        "offsetX": 0,
        "offsetY": -106.667,
        "imagePath": str(resource_dir / "img" / "memorialplace.png"),
        "xDirection": "x+",
        "yDirection": "y-",
        "reverseXY": False,
    },
}

# ============== 纹理定义（资源 -> icon 路径） ==============

ITEM_TEXTURES = {
    "mysekai_material": {
        "1": str(resource_dir / "icon" / "Texture2D" / "item_wood_1.png"),
        "2": str(resource_dir / "icon" / "Texture2D" / "item_wood_2.png"),
        "3": str(resource_dir / "icon" / "Texture2D" / "item_wood_3.png"),
        "4": str(resource_dir / "icon" / "Texture2D" / "item_wood_4.png"),
        "5": str(resource_dir / "icon" / "Texture2D" / "item_wood_5.png"),
        "6": str(resource_dir / "icon" / "Texture2D" / "item_mineral_1.png"),
        "7": str(resource_dir / "icon" / "Texture2D" / "item_mineral_2.png"),
        "8": str(resource_dir / "icon" / "Texture2D" / "item_mineral_3.png"),
        "9": str(resource_dir / "icon" / "Texture2D" / "item_mineral_4.png"),
        "10": str(resource_dir / "icon" / "Texture2D" / "item_mineral_5.png"),
        "11": str(resource_dir / "icon" / "Texture2D" / "item_mineral_6.png"),
        "12": str(resource_dir / "icon" / "Texture2D" / "item_mineral_7.png"),
        "13": str(resource_dir / "icon" / "Texture2D" / "item_junk_1.png"),
        "14": str(resource_dir / "icon" / "Texture2D" / "item_junk_2.png"),
        "15": str(resource_dir / "icon" / "Texture2D" / "item_junk_3.png"),
        "16": str(resource_dir / "icon" / "Texture2D" / "item_junk_4.png"),
        "17": str(resource_dir / "icon" / "Texture2D" / "item_junk_5.png"),
        "18": str(resource_dir / "icon" / "Texture2D" / "item_junk_6.png"),
        "19": str(resource_dir / "icon" / "Texture2D" / "item_junk_7.png"),
        "20": str(resource_dir / "icon" / "Texture2D" / "item_plant_1.png"),
        "21": str(resource_dir / "icon" / "Texture2D" / "item_plant_2.png"),
        "22": str(resource_dir / "icon" / "Texture2D" / "item_plant_3.png"),
        "23": str(resource_dir / "icon" / "Texture2D" / "item_plant_4.png"),
        "24": str(resource_dir / "icon" / "Texture2D" / "item_tone_8.png"),
        "32": str(resource_dir / "icon" / "Texture2D" / "item_junk_8.png"),
        "33": str(resource_dir / "icon" / "Texture2D" / "item_mineral_8.png"),
        "34": str(resource_dir / "icon" / "Texture2D" / "item_junk_9.png"),
        "61": str(resource_dir / "icon" / "Texture2D" / "item_junk_10.png"),
        "62": str(resource_dir / "icon" / "Texture2D" / "item_junk_11.png"),
        "63": str(resource_dir / "icon" / "Texture2D" / "item_junk_12.png"),
        "64": str(resource_dir / "icon" / "Texture2D" / "item_mineral_9.png"),
        "65": str(resource_dir / "icon" / "Texture2D" / "item_mineral_10.png"),
    },
    "mysekai_item": {
        "7": str(resource_dir / "icon" / "Texture2D" / "item_blueprint_fragment.png"),
    },
    "mysekai_fixture": {
        "118": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_118.png"),
        "119": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_119.png"),
        "120": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_120.png"),
        "121": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_121.png"),
        "126": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_126.png"),
        "127": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_127.png"),
        "128": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_128.png"),
        "129": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_129.png"),
        "130": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_130.png"),
        "474": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_474.png"),
        "475": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_475.png"),
        "476": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_476.png"),
        "477": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_477.png"),
        "478": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_478.png"),
        "479": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_479.png"),
        "480": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_480.png"),
        "481": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_481.png"),
        "482": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_482.png"),
        "483": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_483.png"),
    },
    "mysekai_music_record": {
        352: str(resource_dir / "icon" / "Texture2D" / "music352.png"),
    },
}

# ============== 稀有度定义 ==============

RARE_ITEM = {
    "mysekai_material": [5, 12, 20, 24, 32, 33, 61, 62, 63, 64, 65],
    "mysekai_item": [7],
    "mysekai_music_record": [],
    "mysekai_fixture": [118, 119, 120, 121],
}

SUPER_RARE_ITEM = {
    "mysekai_material": [5, 12, 20, 24],
    "mysekai_item": [],
    "mysekai_fixture": [],
    "mysekai_music_record": [],
}

# ============== 字体缓存 ==============

_font_cache: Dict[int, ImageFont.FreeTypeFont] = {}


def get_font(size: int = 8) -> ImageFont.FreeTypeFont:
    """加载字体，带缓存"""
    if size in _font_cache:
        return _font_cache[size]

    font = None

    # 1. 优先尝试加载插件自带的中文字体
    try:
        if plugin_config.font_path.exists():
            font = ImageFont.truetype(str(plugin_config.font_path), size)
        else:
            logger.warning(f"中文字体文件未找到: {plugin_config.font_path}")
    except IOError as e:
        logger.error(f"加载字体失败: {e}")

    # 2. 尝试 Arial
    if font is None:
        try:
            font = ImageFont.truetype("arial.ttf", size)
        except IOError:
            logger.warning("Arial font not found, using default font")

    # 3. 使用默认字体
    if font is None:
        font = ImageFont.load_default()

    _font_cache[size] = font
    return font


# ============== 图标缓存 ==============

_icon_cache: Dict[Tuple[str, Tuple[int, int]], Image.Image] = {}


def get_icon(path: str, size: Tuple[int, int] = (20, 20)) -> Image.Image:
    """加载图标，带缓存"""
    cache_key = (path, size)
    if cache_key in _icon_cache:
        return _icon_cache[cache_key].copy()

    try:
        icon = Image.open(path).convert("RGBA")
        icon = icon.resize(size, Image.Resampling.LANCZOS)
        _icon_cache[cache_key] = icon
        return icon.copy()
    except FileNotFoundError:
        logger.warning(f"Icon not found: {path}, using placeholder.")
        color_hash = hash(path) % 256
        color = (
            (color_hash * 37) % 256,
            (color_hash * 73) % 256,
            (color_hash * 109) % 256,
            255,
        )
        icon = Image.new("RGBA", size, color)
        _icon_cache[cache_key] = icon
        return icon.copy()
