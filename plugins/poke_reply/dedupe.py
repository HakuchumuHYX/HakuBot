"""查重：文本按 difflib 相似度；图片按 dHash 初筛，再缩到同尺寸比 MSE 复核

图片相关函数都是同步的，调用方放进 asyncio.to_thread。
"""

import io
import re
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

from plugins.poke_reply.config import (
    HAMMING_THRESHOLD,
    MSE_THRESHOLD,
    TEXT_SIMILARITY_THRESHOLD,
)

DHASH_SIZE = 8
COMPARE_SIZE = (100, 100)

# 投稿图片文件名是 uuid，不会复用，按路径缓存不用过期
_dhash_cache: dict[Path, np.ndarray] = {}


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).lower()


def similar_text_exists(text: str, existing: list[str]) -> bool:
    normalized = _normalize(text)
    return any(
        SequenceMatcher(None, normalized, _normalize(old)).ratio()
        >= TEXT_SIMILARITY_THRESHOLD
        for old in existing
    )


def _dhash(image: Image.Image) -> np.ndarray:
    gray = image.convert("L").resize(
        (DHASH_SIZE + 1, DHASH_SIZE), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(gray, dtype=np.int16)
    return pixels[:, :-1] > pixels[:, 1:]


def _file_dhash(path: Path) -> np.ndarray:
    if path not in _dhash_cache:
        with Image.open(path) as image:
            _dhash_cache[path] = _dhash(image)
    return _dhash_cache[path]


def _pixels(image: Image.Image) -> np.ndarray:
    resized = image.convert("RGB").resize(COMPARE_SIZE, Image.Resampling.LANCZOS)
    # 转 float 再相减，uint8 相减会回绕
    return np.asarray(resized, dtype=np.float32)


def _same_image(a: Image.Image, b: Image.Image) -> bool:
    return np.mean((_pixels(a) - _pixels(b)) ** 2) < MSE_THRESHOLD


def _close(hash_a: np.ndarray, hash_b: np.ndarray) -> bool:
    return np.count_nonzero(hash_a != hash_b) <= HAMMING_THRESHOLD


def find_duplicate(data: bytes, existing: list[Path]) -> Path | None:
    """新投稿的图片和本群已有图片比，返回重复的那张"""
    with Image.open(io.BytesIO(data)) as new_image:
        new_hash = _dhash(new_image)
        for path in existing:
            if not _close(_file_dhash(path), new_hash):
                continue
            with Image.open(path) as old_image:
                if _same_image(new_image, old_image):
                    return path
    return None


def find_duplicate_pairs(paths: list[Path]) -> list[tuple[Path, Path]]:
    """paths 按投稿先后排好；返回 (保留的, 要删的)，每组保留最早的那张"""
    kept: list[Path] = []
    pairs = []
    for path in paths:
        path_hash = _file_dhash(path)
        for keep in kept:
            if not _close(_file_dhash(keep), path_hash):
                continue
            with Image.open(keep) as a, Image.open(path) as b:
                if _same_image(a, b):
                    pairs.append((keep, path))
                    break
        else:
            kept.append(path)
    return pairs
