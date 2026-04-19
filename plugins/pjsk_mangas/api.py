from __future__ import annotations

import random
from pathlib import Path

from ..utils.moesekai_hub import (
    REPO_DIR,
    ensure_manga_index_ready,
    load_manga_index,
)
from ..utils.tools import get_exc_desc, get_logger
from .models import MangaDetail, MangaSimple

logger = get_logger("pjsk_mangas.api")


async def fetch_manga_list(limit: int = 20) -> list[MangaSimple]:
    await ensure_manga_index_ready()
    data_json = load_manga_index()
    return [MangaSimple(**item) for item in data_json[:limit]]


async def _load_all_mangas() -> list[MangaDetail]:
    await ensure_manga_index_ready()
    data_json = load_manga_index()
    return [MangaDetail(**item) for item in data_json]


def _resolve_local_image_path(manga: MangaDetail) -> Path | None:
    if manga.relative_path:
        path = REPO_DIR / manga.relative_path
        if path.exists():
            return path
    return None


async def fetch_manga_detail(manga_id: str) -> MangaDetail | str:
    try:
        target_id = int(manga_id)
        mangas = await _load_all_mangas()
        for manga in mangas:
            if manga.id == target_id:
                return manga
        return f"未收录 ID 为 {manga_id} 的漫画"
    except Exception as e:
        logger.exception(f"读取漫画 {manga_id} 详情失败: {e}")
        return f"读取漫画数据失败: {get_exc_desc(e)}"


async def fetch_random_manga() -> MangaDetail | str:
    try:
        mangas = await _load_all_mangas()
        if not mangas:
            return "漫画数据为空"
        return random.choice(mangas)
    except Exception as e:
        logger.exception(f"随机获取漫画失败: {e}")
        return f"读取漫画数据失败: {get_exc_desc(e)}"


def get_manga_message_lines(manga: MangaDetail) -> list[str]:
    lines = [
        f"标题：{manga.title}",
        f"原链接：{manga.post_url or '暂无'}",
    ]
    for key, value in manga.contributors.items():
        lines.append(f"{key}：{value}")
    return lines


def get_manga_image_source(manga: MangaDetail) -> str:
    local_path = _resolve_local_image_path(manga)
    if local_path is not None:
        return str(local_path)
    if manga.image_url:
        return manga.image_url
    raise FileNotFoundError(f"漫画 {manga.id} 缺少可用图片")
