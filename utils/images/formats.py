"""Image identification based on actual file contents."""

from io import BytesIO
from PIL import Image, ImageOps

MIME_TYPES = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def extension_for_mime(content_type: str):
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    return next(
        (extension for extension, value in MIME_TYPES.items() if value == mime), None
    )


def detect_format(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    raise ValueError("Unknown image format")


def validate_image(data: bytes) -> str:
    extension = detect_format(data)
    with Image.open(BytesIO(data)) as image:
        image.verify()
    return extension


def to_rgb(image):
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        return Image.alpha_composite(
            Image.new("RGBA", rgba.size, "white"), rgba
        ).convert("RGB")
    return image.convert("RGB")


def image_to_bytes(image, fmt="PNG", **options):
    buffer = BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()
