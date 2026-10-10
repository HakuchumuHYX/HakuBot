"""stickers 的报告图：模板在 templates/，由 Chromium 渲染。

渲染失败返回 None，调用方会改发文字版。
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nonebot.log import logger

from plugins.stickers.send import get_folder_display_info, get_random_sticker
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _image(path: Path) -> dict:
    return {"uri": path.resolve().as_uri(), "name": path.name}


async def render_duplicate_report(
    folder_name: str, duplicates: List[Tuple[Path, Path]]
) -> Optional[bytes]:
    if not duplicates:
        return None
    try:
        return await render_template(
            TEMPLATE_DIR,
            "duplicate.html",
            width=720,
            folder_name=folder_name,
            total=len(duplicates),
            pairs=[(_image(a), _image(b)) for a, b in duplicates[:10]],
        )
    except Exception as e:
        logger.error(f"生成重复报告图片失败: {e}")
        return None


async def _render_cleanup(
    all_duplicates: Dict[str, List[Tuple[Path, Path]]], removed_count: Optional[int]
) -> Optional[bytes]:
    try:
        return await render_template(
            TEMPLATE_DIR,
            "cleanup.html",
            width=600,
            done=removed_count is not None,
            removed_count=removed_count,
            total_pairs=sum(len(dups) for dups in all_duplicates.values()),
            folders=[(name, len(dups)) for name, dups in all_duplicates.items()],
        )
    except Exception as e:
        logger.error(f"生成清理图片失败: {e}")
        return None


async def preview_duplicates_before_cleanup(
    all_duplicates: Dict[str, List[Tuple[Path, Path]]],
) -> Optional[bytes]:
    if not all_duplicates:
        return None
    return await _render_cleanup(all_duplicates, None)


async def render_cleanup_report(
    removed_count: int, all_duplicates: Dict[str, List[Tuple[Path, Path]]]
) -> Optional[bytes]:
    return await _render_cleanup(all_duplicates, removed_count)


async def render_stickers_preview() -> Optional[bytes]:
    folders = sorted(get_folder_display_info(), key=lambda x: x["name"])
    if not folders:
        return None
    for folder in folders:
        cover = get_random_sticker(folder["name"])
        folder["cover"] = cover.resolve().as_uri() if cover else ""

    cols = min(7, len(folders))
    try:
        return await render_template(
            TEMPLATE_DIR,
            "folders.html",
            width=max(640, cols * 216 - 16 + 124),
            cols=cols,
            folders=folders,
            total_images=sum(folder["image_count"] for folder in folders),
        )
    except Exception as e:
        logger.error(f"生成贴图预览图片失败: {e}")
        return None
