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


# 两张对比缩略图一组，太多会让图片过长
MAX_PAIRS = 20


async def _render_duplicates(
    title: str,
    hint: str,
    labels: Tuple[str, str],
    duplicates: Dict[str, List[Tuple[Path, Path]]],
) -> Optional[bytes]:
    folders, shown = [], 0
    for name, pairs in duplicates.items():
        pairs = pairs[: MAX_PAIRS - shown]
        if not pairs:
            break
        folders.append((name, [(_image(a), _image(b)) for a, b in pairs]))
        shown += len(pairs)
    try:
        return await render_template(
            TEMPLATE_DIR,
            "duplicates.html",
            width=760,
            title=title,
            hint=hint,
            labels=labels,
            folders=folders,
            total=sum(len(pairs) for pairs in duplicates.values()),
            shown=shown,
        )
    except Exception as e:
        logger.error(f"生成重复图片报告失败: {e}")
        return None


async def render_duplicate_report(
    folder_name: str, duplicates: List[Tuple[Path, Path]]
) -> Optional[bytes]:
    if not duplicates:
        return None
    return await _render_duplicates(
        "投稿图片已存在",
        "如需强制上传，在投稿指令后加 force",
        ("已有", "投稿"),
        {folder_name: duplicates},
    )


async def preview_duplicates_before_cleanup(
    all_duplicates: Dict[str, List[Tuple[Path, Path]]],
) -> Optional[bytes]:
    if not all_duplicates:
        return None
    return await _render_duplicates(
        "待清理的重复图片",
        "确认后，每组右边的图片会移到备份文件夹",
        ("保留", "移走"),
        all_duplicates,
    )


async def render_stickers_preview() -> Optional[bytes]:
    folders = sorted(get_folder_display_info(), key=lambda x: x["name"])
    if not folders:
        return None
    for folder in folders:
        cover = get_random_sticker(folder["name"])
        folder["cover"] = cover.resolve().as_uri() if cover else ""

    cols = min(5, len(folders))
    try:
        return await render_template(
            TEMPLATE_DIR,
            "folders.html",
            width=max(640, cols * 200),
            cols=cols,
            folders=folders,
            total_images=sum(folder["image_count"] for folder in folders),
        )
    except Exception as e:
        logger.error(f"生成贴图预览图片失败: {e}")
        return None
