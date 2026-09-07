"""Local font lookup shared by browser and PIL rendering."""

from functools import lru_cache
import hashlib
from pathlib import Path
from PIL import ImageFont
from utils.paths import project_root


def font_path(weight: str = "Regular") -> Path:
    candidates = [
        project_root() / "data/shared/fonts" / f"SourceHanSansCN-{weight}.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("No local CJK font in data/shared/fonts or system fonts")


@lru_cache(maxsize=128)
def load_font(size: int, weight: str = "Regular"):
    return ImageFont.truetype(str(font_path(weight)), size)


def font_fingerprint() -> str:
    return hashlib.sha256(
        b"".join(font_path(w).read_bytes() for w in ("Regular", "Bold", "Heavy"))
    ).hexdigest()


def font_css() -> str:
    return "\n".join(
        f"@font-face{{font-family:Haku;src:url('{font_path(w).as_uri()}');font-weight:{n};}}"
        for w, n in (("Regular", 400), ("Bold", 700), ("Heavy", 900))
    )


@lru_cache(maxsize=128)
def load_font_from_path(path: str, size: int):
    candidate = Path(path)
    return ImageFont.truetype(
        str(candidate if candidate.is_file() else font_path()), size
    )
