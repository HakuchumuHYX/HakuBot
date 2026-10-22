"""贴图、字体与图标加载；动态图标和唱片封面的下载缓存"""

import asyncio
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageFont
from nonebot import logger

from utils.json_io import atomic_write_bytes
from utils.logging import get_exc_desc
from utils.network.http import download_bytes
from utils.rendering.fonts import load_font_from_path
from plugins.buaa_msm.config import (
    HARVEST_FIXTURE_ICON_DIR,
    ICON_CACHE_DIR,
    RESOURCE_DIR,
    settings,
)
from plugins.buaa_msm.gamedata import (
    MsrData,
    harvest_fixture_meta,
    icon_asset_name,
    record_jacket_bundle,
)

DOWNLOAD_TIMEOUT = 5.0
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_CONCURRENCY = 8

TEXTURE_DIR = RESOURCE_DIR / "icon" / "Texture2D"
SCENE_IMAGE_DIR = RESOURCE_DIR / "img"
FONT_PATH = RESOURCE_DIR / "font.ttf"
# 唱片在格子里优先画封面，封面没下载到时用这个
DEFAULT_RECORD_ICON = TEXTURE_DIR / "item_surplus_music_record.png"

# 有动态图标的类别：从资源站下载后缓存在 ICON_CACHE_DIR/{category}/{id}.png
_DYNAMIC_CATEGORIES = {
    "material",
    "mysekai_material",
    "mysekai_item",
    "mysekai_fixture",
}

# 动态图标没有缓存时用的静态贴图，值是 TEXTURE_DIR 下的文件名
_STATIC_TEXTURES: dict[str, dict[int, str]] = {
    "mysekai_material": {
        1: "item_wood_1",
        2: "item_wood_2",
        3: "item_wood_3",
        4: "item_wood_4",
        5: "item_wood_5",
        6: "item_mineral_1",
        7: "item_mineral_2",
        8: "item_mineral_3",
        9: "item_mineral_4",
        10: "item_mineral_5",
        11: "item_mineral_6",
        12: "item_mineral_7",
        13: "item_junk_1",
        14: "item_junk_2",
        15: "item_junk_3",
        16: "item_junk_4",
        17: "item_junk_5",
        18: "item_junk_6",
        19: "item_junk_7",
        20: "item_plant_1",
        21: "item_plant_2",
        22: "item_plant_3",
        23: "item_plant_4",
        24: "item_tone_8",
        32: "item_junk_8",
        33: "item_mineral_8",
        34: "item_junk_9",
        61: "item_junk_10",
        62: "item_junk_11",
        63: "item_junk_12",
        64: "item_mineral_9",
        65: "item_mineral_10",
    },
    "mysekai_item": {7: "item_blueprint_fragment"},
    "mysekai_fixture": {
        **{i: f"mdl_non1001_before_sapling1_{i}" for i in range(118, 122)},
        **{
            i: f"mdl_non1001_before_sprout1_{i}"
            for i in [*range(126, 131), *range(474, 484)]
        },
    },
}


def _icon_cache_path(category: str, item_id: int) -> Path:
    return ICON_CACHE_DIR / category / f"{item_id}.png"


def texture_path(category: str, item_id: int) -> Path | None:
    if category == "mysekai_music_record":
        return DEFAULT_RECORD_ICON
    cached = _icon_cache_path(category, item_id)
    if cached.exists():
        return cached
    name = _STATIC_TEXTURES.get(category, {}).get(item_id)
    return TEXTURE_DIR / f"{name}.png" if name else None


def harvest_fixture_icon(fixture_id: int) -> Path | None:
    """收获点本体贴图；没有时位置图回退成圆点"""
    meta = harvest_fixture_meta(fixture_id)
    if meta is None:
        return None
    asset, rarity = meta
    path = HARVEST_FIXTURE_ICON_DIR / rarity / f"{asset}.png"
    return path if path.exists() else None


def get_font(size: int) -> ImageFont.FreeTypeFont:
    """插件字体缺失时 load_font_from_path 会回退到公共思源黑体"""
    return load_font_from_path(str(FONT_PATH), size)


_icon_cache: dict[tuple[Path, tuple[int, int]], Image.Image] = {}


def get_icon(path: Path, size: tuple[int, int]) -> Image.Image:
    """加载并缩放图标，带缓存；文件不存在时给一个纯色占位块"""
    key = (path, size)
    if key not in _icon_cache:
        try:
            icon = Image.open(path).convert("RGBA")
            _icon_cache[key] = icon.resize(size, Image.Resampling.LANCZOS)
        except FileNotFoundError:
            logger.warning(f"Icon not found: {path}, using placeholder.")
            seed = hash(str(path)) % 256
            color = ((seed * 37) % 256, (seed * 73) % 256, (seed * 109) % 256, 255)
            _icon_cache[key] = Image.new("RGBA", size, color)
    return _icon_cache[key].copy()


# ============== 下载 ==============


def _asset_url(stage: str, relative_path: str) -> str:
    base = settings.asset.url_base.rstrip("/")
    return f"{base}/{settings.asset.server}-assets/{stage}/{relative_path}"


def _icon_urls(category: str, item_id: int) -> list[str]:
    # 普通 material 的路径不依赖 masterdata，且在 startapp 里
    if category == "material":
        relative = f"thumbnail/material/material{item_id}.png"
        return [_asset_url("startapp", relative), _asset_url("ondemand", relative)]

    asset = icon_asset_name(category, item_id)
    if not asset:
        return []
    relatives = {
        "mysekai_material": [f"mysekai/thumbnail/material/{asset}.png"],
        "mysekai_item": [f"mysekai/thumbnail/item/{asset}.png"],
        "mysekai_fixture": [
            f"mysekai/thumbnail/fixture/{asset}_{item_id}_1.png",
            f"mysekai/thumbnail/fixture/{asset}_1.png",
        ],
    }[category]
    # mysekai 前缀的资源在 ondemand；startapp 留作资源迁移时的兜底
    return [
        _asset_url(stage, relative)
        for relative in relatives
        for stage in ("ondemand", "startapp")
    ]


def _headers() -> dict[str, str]:
    # 资源站对没有 UA / Referer 的直连请求返回 403；Referer 用资源站自身
    parsed = urlsplit(settings.asset.url_base)
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }


async def _download_first(urls: list[str]) -> bytes | None:
    """依次尝试候选地址，返回第一个成功的；404 等不重试，直接换下一个"""
    for url in urls:
        try:
            return await download_bytes(
                url,
                headers=_headers(),
                timeout=DOWNLOAD_TIMEOUT,
                attempts=DOWNLOAD_ATTEMPTS,
            )
        except Exception as exc:
            logger.debug(f"资源下载失败 {url}: {get_exc_desc(exc)}")
    return None


async def _fetch_icon(category: str, item_id: int) -> bool:
    data = await _download_first(_icon_urls(category, item_id))
    if data is None:
        return False
    atomic_write_bytes(_icon_cache_path(category, item_id), data)
    return True


async def _fetch_jacket(record_id: int) -> Image.Image | None:
    bundle = record_jacket_bundle(record_id)
    if not bundle:
        return None
    data = await _download_first(
        [_asset_url("startapp", f"music/jacket/{bundle}/{bundle}.png")]
    )
    if data is None:
        return None
    try:
        return Image.open(BytesIO(data)).convert("RGBA")
    except OSError as exc:
        logger.debug(f"唱片封面解码失败 {record_id}: {get_exc_desc(exc)}")
        return None


async def prefetch(data: MsrData) -> dict[int, Image.Image]:
    """下载这份数据用到、本地还没缓存的动态图标，以及唱片封面；返回 {唱片 id: 封面}。

    下载失败的图标渲染时用静态贴图，没有封面的唱片用默认唱片图标。
    """
    items = {key for summary in data.materials.values() for key in summary}
    icons = sorted(
        (category, item_id)
        for category, item_id in items
        if category in _DYNAMIC_CATEGORIES
        and not _icon_cache_path(category, item_id).exists()
    )
    records = sorted(
        item_id for category, item_id in items if category == "mysekai_music_record"
    )

    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

    async def limited(coroutine):
        async with semaphore:
            return await coroutine

    icon_results, jacket_results = await asyncio.gather(
        asyncio.gather(*(limited(_fetch_icon(c, i)) for c, i in icons)),
        asyncio.gather(*(limited(_fetch_jacket(r)) for r in records)),
    )

    failed = [icon for icon, ok in zip(icons, icon_results) if not ok]
    logger.debug(f"MySekai 动态 icon 下载完成: {len(icons) - len(failed)}/{len(icons)}")
    if failed:
        logger.warning(
            f"MySekai 动态 icon 下载失败 {len(failed)} 个，将用静态贴图: {failed[:5]}"
        )
    jackets = {
        record_id: image
        for record_id, image in zip(records, jacket_results)
        if image is not None
    }
    logger.debug(f"唱片封面下载完成: {len(jackets)}/{len(records)}")
    if records and not jackets:
        logger.warning("唱片封面全部下载失败，将用默认唱片图标")
    return jackets
