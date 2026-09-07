from dataclasses import dataclass
import asyncio
import hashlib
from utils.images.formats import validate_image, MIME_TYPES
from .http import download_bytes


@dataclass(frozen=True)
class DownloadedImage:
    data: bytes
    extension: str

    @property
    def mime_type(self):
        return MIME_TYPES[self.extension]

    @property
    def md5(self):
        return hashlib.md5(self.data).hexdigest()


async def fetch_image(
    url,
    *,
    max_bytes=10 * 1024 * 1024,
    timeout=15,
    allowed_formats=("jpg", "png", "gif", "webp"),
    **options,
):
    data = await download_bytes(url, max_bytes=max_bytes, timeout=timeout, **options)
    extension = await asyncio.to_thread(validate_image, data)
    if extension not in allowed_formats:
        raise ValueError(f"Unsupported image format: {extension}")
    return DownloadedImage(data, extension)
