"""stickers 的图：重复报告、文件夹预览走 Chromium 模板（templates/）；
看所有的概览图是上千张缩略图拼接，留在 PIL。

模板渲染失败返回 None，调用方只发文字部分。
"""

import base64
import math
import random
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from nonebot import logger
from PIL import Image, ImageDraw

from plugins.stickers import store
from utils.images.formats import to_rgb
from utils.network.images import DownloadedImage
from utils.rendering.engine import render_template
from utils.rendering.fonts import load_font

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 两张对比缩略图一组，太多会让图片过长
MAX_PAIRS = 20


def _file_image(path: Path) -> dict:
    return {"uri": path.resolve().as_uri(), "name": path.name}


def _data_image(image: DownloadedImage) -> dict:
    encoded = base64.b64encode(image.data).decode()
    return {"uri": f"data:{image.mime_type};base64,{encoded}", "name": ""}


async def _render_duplicates(
    title: str,
    hint: str,
    labels: tuple[str, str],
    duplicates: dict[str, list[tuple[dict, dict]]],
) -> bytes | None:
    folders, shown = [], 0
    for name, pairs in duplicates.items():
        pairs = pairs[: MAX_PAIRS - shown]
        if not pairs:
            break
        folders.append((name, pairs))
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
        logger.opt(exception=True).warning(f"生成重复图片报告失败: {e}")
        return None


async def contribution_duplicates(
    folder: str, pairs: list[tuple[Path, DownloadedImage]]
) -> bytes | None:
    return await _render_duplicates(
        "投稿图片已存在",
        "如需强制上传，在投稿指令后加 force",
        ("已有", "投稿"),
        {folder: [(_file_image(old), _data_image(new)) for old, new in pairs]},
    )


async def cleanup_preview(
    duplicates: dict[str, list[tuple[Path, Path]]],
) -> bytes | None:
    return await _render_duplicates(
        "待清理的重复图片",
        "确认后，每组右边的图片会移到备份文件夹",
        ("保留", "移走"),
        {
            folder: [(_file_image(a), _file_image(b)) for a, b in pairs]
            for folder, pairs in duplicates.items()
        },
    )


async def folders_preview() -> bytes | None:
    folders = []
    for name in sorted(store.folders):
        files = store.images(name)
        folders.append(
            {
                "name": name,
                "aliases": store.folders[name],
                "image_count": len(files),
                "cover": random.choice(files).resolve().as_uri() if files else "",
            }
        )
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
        logger.opt(exception=True).warning(f"生成贴图预览图片失败: {e}")
        return None


# --- 看所有：PIL 拼概览图，同步函数，调用方放进线程 ---

# 一页最多 2000 张：80px 缩略图时画布约 4000×5000
OVERVIEW_PAGE_SIZE = 2000


def _thumbnail(path: Path, size: int) -> Image.Image | None:
    try:
        with Image.open(path) as image:
            # 动图只取第一帧
            thumb = to_rgb(image)
        thumb.thumbnail((size, size), Image.Resampling.BILINEAR)
        return thumb
    except Exception as e:
        logger.debug(f"概览图加载失败 {path}: {e}")
        return None


def gallery_overview(
    files: list[Path], folder: str, page: int, total_pages: int
) -> bytes:
    total = len(files)
    # 列数约等于 sqrt(总数)，图越多缩略图越小
    cols = max(5, math.ceil(math.sqrt(total)))
    rows = math.ceil(total / cols)
    thumb_size, font_size = next(
        (size, font)
        for limit, size, font in (
            (25, 200, 24),
            (100, 160, 20),
            (400, 120, 16),
            (900, 100, 14),
            (math.inf, 80, 12),
        )
        if total <= limit
    )

    padding = 10
    header_height = 60
    cell_w = thumb_size + padding
    cell_h = thumb_size + font_size + 10 + padding
    canvas = Image.new(
        "RGB",
        (cols * cell_w + padding, rows * cell_h + padding + header_height),
        color=(245, 247, 250),
    )
    draw = ImageDraw.Draw(canvas)
    text_font = load_font(font_size)

    title = f"Gallery: {folder} ({total} items)"
    if total_pages > 1:
        title += f" - Page {page}/{total_pages}"
    draw.text((20, 15), title, fill=(50, 50, 50), font=load_font(32))

    # 解码是大头，并行加载；粘贴只在主线程做
    with ThreadPoolExecutor(max_workers=min(16, max(4, total // 50))) as executor:
        thumbs = executor.map(lambda path: _thumbnail(path, thumb_size), files)

        for index, (path, thumb) in enumerate(zip(files, thumbs)):
            x = padding + index % cols * cell_w
            y = header_height + padding + index // cols * cell_h
            if thumb:
                canvas.paste(
                    thumb,
                    (
                        x + (thumb_size - thumb.width) // 2,
                        y + (thumb_size - thumb.height) // 2,
                    ),
                )
            else:
                draw.rectangle(
                    [x, y, x + thumb_size, y + thumb_size], fill=(220, 220, 220)
                )
                draw.text(
                    (x + 5, y + thumb_size // 2), "Error", fill="red", font=text_font
                )
            label_w = draw.textlength(path.stem, font=text_font)
            draw.text(
                (x + (thumb_size - label_w) // 2, y + thumb_size + 2),
                path.stem,
                fill=(80, 80, 80),
                font=text_font,
            )

    output = BytesIO()
    canvas.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()
