"""图片查重：dHash 初筛，再缩到同尺寸比 MSE 复核

函数都是同步的，调用方放进 asyncio.to_thread。
动图帧数不同直接算不重复（动图和它的静态截图也不算）；帧数相同时复核首、中、尾三帧，
表情库里有不少开头相同、后半段才分开的动图。
"""

import io
import warnings
from pathlib import Path

import numpy as np
from PIL import Image

from utils.images.formats import to_rgb

# 部分表情包的 EXIF 是坏的，不影响取像素
warnings.filterwarnings("ignore", message="Corrupt EXIF data")

# 在表情库和戳一戳投稿图上实测：重复图（重新编码、帧错位）大多在 40 以下，
# 同一套背景、图标不同的表情落在 50~100；40~100 两类交叠，局部小改动（缺一口、换个数字）
# 平摊到整图上和编码噪点差不多，整图 MSE 分不开，误判靠调用方的确认/强制上传兜住
HAMMING_THRESHOLD = 5
MSE_THRESHOLD = 50

DHASH_SIZE = 8
COMPARE_SIZE = (100, 100)

# 签名是 (dHash, 帧数)
Signature = tuple[int, int]

# 路径 -> (mtime_ns, size, 签名)；文件被替换或重命名复用路径时按 mtime/size 重算
_signature_cache: dict[Path, tuple[int, int, Signature]] = {}


def _signature(image: Image.Image) -> Signature:
    # 透明图先铺白底：直接转灰度会露出透明区域里藏着的底色
    gray = (
        to_rgb(image)
        .convert("L")
        .resize((DHASH_SIZE + 1, DHASH_SIZE), Image.Resampling.LANCZOS)
    )
    pixels = np.asarray(gray, dtype=np.int16)
    bits = (pixels[:, :-1] > pixels[:, 1:]).flatten()
    dhash = int.from_bytes(np.packbits(bits).tobytes(), "big")
    return dhash, getattr(image, "n_frames", 1)


def _file_signature(path: Path) -> Signature:
    stat = path.stat()
    cached = _signature_cache.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    with Image.open(path) as image:
        value = _signature(image)
    _signature_cache[path] = (stat.st_mtime_ns, stat.st_size, value)
    return value


def warm_cache(paths: list[Path]) -> None:
    """提前把签名算进缓存，免得第一次查重时现算（大图库冷算要十几秒）"""
    for path in paths:
        _file_signature(path)


def _close(a: Signature, b: Signature) -> bool:
    return a[1] == b[1] and (a[0] ^ b[0]).bit_count() <= HAMMING_THRESHOLD


def _pixels(image: Image.Image) -> np.ndarray:
    resized = to_rgb(image).resize(COMPARE_SIZE, Image.Resampling.LANCZOS)
    # 转 float 再相减，uint8 相减会回绕
    return np.asarray(resized, dtype=np.float32)


def _same_frame(a: Image.Image, b: Image.Image, index: int) -> bool:
    a.seek(index)
    b.seek(index)
    return np.mean((_pixels(a) - _pixels(b)) ** 2) < MSE_THRESHOLD


def _same_image(a: Image.Image, b: Image.Image) -> bool:
    """调用前 _close 已保证两边帧数相同"""
    frames = getattr(a, "n_frames", 1)
    indexes = sorted({0, frames // 2, frames - 1})
    return all(_same_frame(a, b, index) for index in indexes)


def find_duplicate(source: Path | bytes, existing: list[Path]) -> Path | None:
    """source 和 existing 里的哪张重复；没有返回 None"""
    opened = io.BytesIO(source) if isinstance(source, bytes) else source
    with Image.open(opened) as new_image:
        new_signature = _signature(new_image)
        for path in existing:
            if not _close(_file_signature(path), new_signature):
                continue
            with Image.open(path) as old_image:
                if _same_image(new_image, old_image):
                    return path
    return None


def find_duplicate_pairs(paths: list[Path]) -> list[tuple[Path, Path]]:
    """paths 由调用方按先后排好；返回 (保留的, 重复的)，每组保留最早那张"""
    kept: list[tuple[Path, Signature]] = []
    pairs = []
    for path in paths:
        signature = _file_signature(path)
        for keep, keep_signature in kept:
            if not _close(keep_signature, signature):
                continue
            with Image.open(keep) as a, Image.open(path) as b:
                if _same_image(a, b):
                    pairs.append((keep, path))
                    break
        else:
            kept.append((path, signature))
    return pairs
