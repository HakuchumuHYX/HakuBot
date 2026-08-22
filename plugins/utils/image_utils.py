from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import shutil
import tempfile
import time
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import TypeAlias
from urllib.parse import unquote, urlparse

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger


ImageSource: TypeAlias = str | Path | bytes | bytearray | BytesIO

_KNOWN_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_OUTBOUND_MEDIA_TTL_SECONDS = 24 * 60 * 60
_CLEAN_INTERVAL_SECONDS = 6 * 60 * 60
_cleanup_lock = Lock()
_last_cleanup_at = 0.0
_cleanup_task: asyncio.Task | None = None
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ImagePreparationError(ValueError):
    """Raised when an outgoing image cannot be made readable by OneBot."""


def _host_root() -> Path:
    return Path(os.getenv("NAPCAT_SHARED_HOST_ROOT", str(_PROJECT_ROOT))).resolve()


def _container_root() -> Path:
    return Path(os.getenv("NAPCAT_SHARED_CONTAINER_ROOT", str(_PROJECT_ROOT)))


def _outbound_media_dir() -> Path:
    path = (_host_root() / "cache" / "outbound_media").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _detect_image_suffix(data: bytes, filename: str | None = None) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"

    suffix = Path(filename or "").suffix.lower()
    if suffix in _KNOWN_IMAGE_SUFFIXES:
        return ".jpg" if suffix == ".jpeg" else suffix
    raise ImagePreparationError("无法识别图片格式")


def _validate_file(path: Path) -> Path:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {path}")
    if not path.is_file():
        raise ImagePreparationError(f"图片路径不是文件: {path}")
    if path.stat().st_size <= 0:
        raise ImagePreparationError(f"图片文件为空: {path}")
    return path


def _write_cached_image(data: bytes, filename: str | None = None) -> Path:
    if not data:
        raise ImagePreparationError("图片数据为空")

    suffix = _detect_image_suffix(data, filename)
    digest = hashlib.sha256(data).hexdigest()
    target = _outbound_media_dir() / f"{digest}{suffix}"
    if target.exists() and target.stat().st_size == len(data):
        target.touch()
        return target

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{digest}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            Path(temporary_name).unlink(missing_ok=True)
        except OSError:
            pass
    return target


def _cache_external_file(path: Path) -> Path:
    path = _validate_file(path)
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)

    with path.open("rb") as source_file:
        suffix = _detect_image_suffix(source_file.read(16), path.name)

    target = _outbound_media_dir() / f"{digest.hexdigest()}{suffix}"
    if target.exists() and target.stat().st_size == path.stat().st_size:
        target.touch()
        return target

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{digest.hexdigest()}.", suffix=".tmp", dir=target.parent
    )
    os.close(fd)
    try:
        shutil.copyfile(path, temporary_name)
        os.replace(temporary_name, target)
    finally:
        try:
            Path(temporary_name).unlink(missing_ok=True)
        except OSError:
            pass
    return target


def _host_path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.netloc not in {"", "localhost"}:
        raise ImagePreparationError(f"不支持的 file URI 主机: {parsed.netloc}")

    uri_path = Path(unquote(parsed.path))
    container_root = _container_root()
    if _is_relative_to(uri_path, container_root):
        return _host_root() / uri_path.relative_to(container_root)
    return uri_path


def _decode_encoded_image(source: str, filename: str | None) -> Path:
    try:
        if source.startswith("base64://"):
            encoded = source[len("base64://") :]
            return _write_cached_image(base64.b64decode(encoded, validate=True), filename)

        header, separator, encoded = source.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ImagePreparationError("图片 data URI 必须使用 base64 编码")
        mime = header[5:].split(";", maxsplit=1)[0].lower()
        mime_suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
        }.get(mime)
        return _write_cached_image(
            base64.b64decode(encoded, validate=True), filename or f"image{mime_suffix or ''}"
        )
    except (binascii.Error, ValueError) as exc:
        if isinstance(exc, ImagePreparationError):
            raise
        raise ImagePreparationError("图片 base64 数据无效") from exc


def cleanup_outbound_media(*, now: float | None = None) -> int:
    """Delete expired files created by this module and return the removed count."""
    cutoff = (time.time() if now is None else now) - _OUTBOUND_MEDIA_TTL_SECONDS
    removed = 0
    for path in _outbound_media_dir().iterdir():
        try:
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning(f"[outbound_media] 清理失败 path={path}: {exc}")
    return removed


def _maybe_cleanup_outbound_media() -> None:
    global _last_cleanup_at
    now = time.time()
    if now - _last_cleanup_at < _CLEAN_INTERVAL_SECONDS:
        return
    with _cleanup_lock:
        if now - _last_cleanup_at < _CLEAN_INTERVAL_SECONDS:
            return
        removed = cleanup_outbound_media(now=now)
        _last_cleanup_at = now
        if removed:
            logger.info(f"[outbound_media] 已清理 {removed} 个过期文件")


def _container_path_for_host_file(path: Path) -> Path:
    path = _validate_file(path)
    host_root = _host_root()
    if not _is_relative_to(path, host_root):
        path = _cache_external_file(path)
    if not _is_relative_to(path, host_root):
        raise ImagePreparationError(
            f"共享媒体缓存不在 NAPCAT_SHARED_HOST_ROOT 内: {path}"
        )
    return _container_root() / path.relative_to(host_root)


def prepare_image_source(
    source: ImageSource,
    *,
    filename: str | None = None,
) -> str | Path:
    """Prepare an outgoing image as a URL or a NapCat-readable local path."""
    _maybe_cleanup_outbound_media()

    if isinstance(source, BytesIO):
        source = source.getvalue()
    if isinstance(source, bytearray):
        source = bytes(source)
    if isinstance(source, bytes):
        return _container_path_for_host_file(_write_cached_image(source, filename))
    if isinstance(source, Path):
        return _container_path_for_host_file(source)
    if not isinstance(source, str):
        raise TypeError(f"不支持的图片来源类型: {type(source).__name__}")

    source = source.strip()
    if not source:
        raise ImagePreparationError("图片来源为空")
    if source.startswith(("base64://", "data:")):
        return _container_path_for_host_file(_decode_encoded_image(source, filename))

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        return source
    if parsed.scheme == "file":
        return _container_path_for_host_file(_host_path_from_file_uri(source))
    if parsed.scheme:
        raise ImagePreparationError(f"不支持的图片 URI scheme: {parsed.scheme}")
    return _container_path_for_host_file(Path(source))


def image_segment(
    source: ImageSource,
    *,
    filename: str | None = None,
    **segment_options,
) -> MessageSegment:
    """Build a OneBot image segment without embedding local data in the request."""
    return MessageSegment.image(
        prepare_image_source(source, filename=filename),
        **segment_options,
    )


try:
    _driver = get_driver()
except ValueError:
    _driver = None

if _driver is not None:

    @_driver.on_startup
    async def _start_outbound_media_cleanup() -> None:
        global _cleanup_task, _last_cleanup_at
        removed = cleanup_outbound_media()
        _last_cleanup_at = time.time()
        if removed:
            logger.info(f"[outbound_media] 启动清理了 {removed} 个过期文件")

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(_CLEAN_INTERVAL_SECONDS)
                _maybe_cleanup_outbound_media()

        _cleanup_task = asyncio.create_task(cleanup_loop())

    @_driver.on_shutdown
    async def _stop_outbound_media_cleanup() -> None:
        global _cleanup_task
        if _cleanup_task is None:
            return
        _cleanup_task.cancel()
        _cleanup_task = None


def path_to_base64_record(path) -> MessageSegment:
    """Keep the legacy audio transport until records are migrated separately."""
    source_path = _validate_file(Path(path))
    encoded = base64.b64encode(source_path.read_bytes()).decode("utf-8")
    return MessageSegment.record(f"base64://{encoded}")
