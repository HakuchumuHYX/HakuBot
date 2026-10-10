"""本地 CJK 字体，浏览器模板（font_css）和少量 PIL 贴图合成共用。"""

from functools import lru_cache
from pathlib import Path
from PIL import ImageFont
from utils.paths import project_root

FONT_DIR = project_root() / "data/shared/fonts"
# 每个字重一个真实文件；font-weight 只有在对应文件存在时才生效，缺了浏览器会全部渲染成常规体
WEIGHTS = {
    "Regular": 400,
    "Medium": 500,
    "Semibold": 600,
    "Bold": 700,
    "Heavy": 900,
}


def font_path(weight: str = "Regular") -> Path:
    candidates = [
        FONT_DIR / f"MiSans-{weight}.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("No local CJK font in data/shared/fonts or system fonts")


@lru_cache(maxsize=1)
def font_css() -> str:
    return "\n".join(
        f"@font-face{{font-family:Haku;src:url('{font_path(w).as_uri()}');font-weight:{n};}}"
        for w, n in WEIGHTS.items()
    )


@lru_cache(maxsize=128)
def load_font(size: int, weight: str = "Regular"):
    return ImageFont.truetype(str(font_path(weight)), size)


@lru_cache(maxsize=128)
def load_font_from_path(path: str, size: int):
    candidate = Path(path)
    return ImageFont.truetype(
        str(candidate if candidate.is_file() else font_path()), size
    )
