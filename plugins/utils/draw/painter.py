from typing import Union, Tuple, List, Optional, Dict, Any, get_type_hints
from PIL import Image, ImageFont, ImageDraw, ImageFilter, ImageChops
from PIL.ImageFont import ImageFont as Font
from dataclasses import dataclass, is_dataclass, fields
import os
import numpy as np
import math
import emoji

# 修复 pilmoji 与新版 emoji 库的兼容性
# 无论 unicode_codes 是否存在，都确保它有 get_emoji_unicode_dict 方法
if not hasattr(emoji, 'unicode_codes'):
    class MockUnicodeCodes:
        pass
    emoji.unicode_codes = MockUnicodeCodes

if not hasattr(emoji.unicode_codes, 'get_emoji_unicode_dict'):
    def _get_emoji_unicode_dict(lang):
        return {data[lang]: emj for emj, data in emoji.EMOJI_DATA.items() 
                if lang in data and data['status'] <= emoji.STATUS['fully_qualified']}
    
    if isinstance(emoji.unicode_codes, type):
        emoji.unicode_codes.get_emoji_unicode_dict = staticmethod(_get_emoji_unicode_dict)
    else:
        emoji.unicode_codes.get_emoji_unicode_dict = _get_emoji_unicode_dict

from pilmoji import Pilmoji
from pilmoji import getsize as getsize_emoji
from pilmoji.source import GoogleEmojiSource
from datetime import datetime, timedelta
import asyncio
import colorsys
import random
import hashlib
import pickle
import glob
import io
import colour
import struct
from concurrent.futures import ThreadPoolExecutor

from ..tools import get_logger
from .img_utils import adjust_image_alpha_inplace, save_transparent_static_gif

logger = get_logger("Painter")

def deterministic_hash(obj: Any, raise_error: bool = False) -> str:
    """
    计算复杂对象的确定性哈希值。
    """
    hasher = hashlib.md5()
    # 用于检测循环引用：id(obj) -> recursion_depth
    seen = set()
    
    # 预编译 struct 格式，提高性能
    STRUCT_BOOL = struct.Struct('?') 
    STRUCT_FLOAT = struct.Struct('>d') # Big-endian float
    STRUCT_Q = struct.Struct('>Q')     # Big-endian unsigned long long (8 bytes)

    def _update_bytes(b: bytes):
        hasher.update(b)

    def _update_str(s: str):
        b = s.encode('utf-8')
        _update_bytes(STRUCT_Q.pack(len(b)))
        _update_bytes(b)

    def _serialize(o: Any):
        # 处理循环引用
        oid = id(o)
        if oid in seen:
            _update_bytes(b'<RECURSION>')
            return
        
        is_container = isinstance(o, (dict, list, tuple, set, frozenset)) or hasattr(o, '__dict__') or hasattr(o, '__slots__')
        if is_container:
            seen.add(oid)

        try:
            if o is None:
                _update_bytes(b'N')
            elif isinstance(o, bool):
                _update_bytes(b'B')
                _update_bytes(STRUCT_BOOL.pack(o))
            elif isinstance(o, int):
                _update_bytes(b'I')
                try:
                    _update_bytes(STRUCT_Q.pack(o) if o >= 0 else struct.pack('>q', o))
                except struct.error:
                    _update_bytes(hex(o).encode('ascii'))
            elif isinstance(o, float):
                _update_bytes(b'F')
                if np.isnan(o):
                    _update_bytes(b'nan')
                else:
                    _update_bytes(STRUCT_FLOAT.pack(o))
            elif isinstance(o, str):
                _update_bytes(b'S')
                _update_str(o)
            elif isinstance(o, (bytes, bytearray)):
                _update_bytes(b'Y')
                _update_bytes(STRUCT_Q.pack(len(o)))
                _update_bytes(o)
            elif isinstance(o, (list, tuple)):
                _update_bytes(b'L' if isinstance(o, list) else b'T')
                _update_bytes(STRUCT_Q.pack(len(o)))
                for item in o:
                    _serialize(item)
            elif isinstance(o, dict):
                _update_bytes(b'D')
                _update_bytes(STRUCT_Q.pack(len(o)))
                kvs = []
                for k, v in o.items():
                    try:
                        k_repr = k if isinstance(k, (str, int, float, bool)) else str(k)
                        kvs.append((k_repr, k, v))
                    except:
                        kvs.append((id(k), k, v))
                kvs.sort(key=lambda x: x[0])
                for _, k, v in kvs:
                    _serialize(k)
                    _serialize(v)
            elif isinstance(o, Image.Image):
                _update_bytes(b'P')
                _update_str(f"{o.size}:{o.mode}")
                _update_bytes(STRUCT_Q.pack(len(o.tobytes())))
                _update_bytes(o.tobytes())
            elif is_dataclass(o) and not isinstance(o, type):
                _update_bytes(b'C')
                _update_str(o.__class__.__name__)
                for field in fields(o):
                    _serialize(field.name)
                    _serialize(getattr(o, field.name))
            else:
                _update_bytes(b'O')
                _update_str(f"{o.__class__.__module__}.{o.__class__.__name__}")
                data_to_hash = []
                if hasattr(o, '__dict__'):
                    data_to_hash += sorted([(k, v) for k, v in o.__dict__.items() if not k.startswith('_')])
                if not data_to_hash and hasattr(o, '__iter__'):
                     _update_bytes(b'ITER')
                     for i in o:
                         _serialize(i)
                else:
                    data_to_hash.sort(key=lambda x: x[0])
                    for k, v in data_to_hash:
                        _serialize(k)
                        _serialize(v)
                    if not data_to_hash:
                        if raise_error:
                            raise TypeError(f"Object of type {type(o)} is not hashable")
                        else:
                            _update_bytes(b'UNKNOWN')
        finally:
            if is_container:
                seen.remove(oid)

    _serialize(obj)
    return hasher.hexdigest()

# =========================== 基础定义 =========================== #

PAINTER_CACHE_DIR = "data/utils/painter_cache/"

Color = Tuple[int, int, int, int]
Position = Tuple[int, int]
Size = Tuple[int, int]
LchColor = Tuple[float, float, float]

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
TRANSPARENT = (0, 0, 0, 0)
SHADOW = (0, 0, 0, 150)

# 字体目录（通用）
FONT_DIR = "data/utils/fonts/"
# 兼容旧部署：搜图插件早期用的是 data/lunabot_imgexp/fonts/
LEGACY_FONT_DIR = "data/lunabot_imgexp/fonts/"

DEFAULT_FONT = "SourceHanSansCN-Regular"
DEFAULT_BOLD_FONT = "SourceHanSansCN-Bold"
DEFAULT_HEAVY_FONT = "SourceHanSansCN-Heavy"
DEFAULT_EMOJI_FONT = "EmojiOneColor-SVGinOT"

ALIGN_MAP = {
    'c': ('c', 'c'), 'l': ('l', 'c'), 'r': ('r', 'c'), 't': ('c', 't'), 'b': ('c', 'b'),
    'tl': ('l', 't'), 'tr': ('r', 't'), 'bl': ('l', 'b'), 'br': ('r', 'b'),
    'lt': ('l', 't'), 'lb': ('l', 'b'), 'rt': ('r', 't'), 'rb': ('r', 'b'), 
}

# =========================== 工具函数 =========================== #

@dataclass
class FontDesc:
    path: str
    size: int

@dataclass
class FontCacheEntry:
    font: Font
    last_used: datetime

font_cache: dict[str, FontCacheEntry] = {}
font_std_size_cache: dict[Font, Size] = {}

def crop_by_align(original_size, crop_size, align):
    w, h = original_size
    cw, ch = crop_size
    assert cw <= w and ch <= h, "Crop size must be smaller than original size"
    x, y = 0, 0
    xa, ya = ALIGN_MAP[align]
    if xa == 'l':
        x = 0
    elif xa == 'r':
        x = w - cw
    elif xa == 'c':
        x = (w - cw) // 2
    if ya == 't':
        y = 0
    elif ya == 'b':
        y = h - ch
    elif ya == 'c':
        y = (h - ch) // 2
    return x, y, x + cw, y + ch

def lerp_color(c1, c2, t):
    ret = []
    for i in range(len(c1)):
        ret.append(max(0, min(255, int(c1[i] * (1 - t) + c2[i] * t))))
    return tuple(ret)

def lerp_lch(c1: LchColor, c2: LchColor, t: float) -> LchColor:
    l = c1[0] * (1 - t) + c2[0] * t
    c = c1[1] * (1 - t) + c2[1] * t
    h1, h2 = c1[2], c2[2]
    if abs(h2 - h1) > 0.5:
        if h1 > h2:
            h2 += 1.0
        else:
            h1 += 1.0
    h = (h1 * (1 - t) + h2 * t) % 360.0
    return l, c, h

def adjust_color(c, r=None, g=None, b=None, a=None):
    c = list(c)
    if len(c) == 3: c.append(255)
    if r is not None: c[0] = r
    if g is not None: c[1] = g
    if b is not None: c[2] = b
    if a is not None: c[3] = a
    return tuple(c)

def get_font(path: str, size: int) -> Font:
    global font_cache
    key = f"{path}_{size}"

    # 支持：
    # - 直接传入绝对/相对路径
    # - 传入字体名（不带后缀），从 FONT_DIR / LEGACY_FONT_DIR 下查找
    candidates = [path]
    for base in (FONT_DIR, LEGACY_FONT_DIR):
        candidates.append(os.path.join(base, path))
        candidates.append(os.path.join(base, path + ".ttf"))
        candidates.append(os.path.join(base, path + ".otf"))

    if key not in font_cache:
        font = None
        for candidate in candidates:
            if os.path.exists(candidate):
                font = ImageFont.truetype(candidate, size)
                break

        if font is None:
            # 尝试使用系统默认字体或 fallback
            try:
                font = ImageFont.truetype("arial.ttf", size)
            except Exception:
                font = ImageFont.load_default()
            logger.warning(f"Font file not found: {path}, using default")

        font_cache[key] = FontCacheEntry(
            font=font,
            last_used=datetime.now(),
        )
        # 清理过期的字体缓存
        while len(font_cache) > 20:
            oldest_key = min(font_cache, key=lambda k: font_cache[k].last_used)
            removed = font_cache.pop(oldest_key)
            font_std_size_cache.pop(removed.font, None)
    return font_cache[key].font

def get_font_std_size(font: Font) -> Size:
    global font_std_size_cache
    if font not in font_std_size_cache:
        std_size = get_text_size(font, "哇")
        font_std_size_cache[font] = std_size
        return std_size
    return font_std_size_cache[font]

def get_font_desc(path: str, size: int) -> FontDesc:
    return FontDesc(path, size)

def has_emoji(text: str) -> bool:
    for c in text:
        if c in emoji.EMOJI_DATA:
            return True
    return False

def get_text_size(font: Font, text: str) -> Size:
    if not text: 
        return (0, 0)
    if has_emoji(text):
        return getsize_emoji(text, font=font, emoji_scale_factor=1.0)
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def oklch_to_srgb(lch_colors: np.ndarray) -> np.ndarray:
    oklch_color = lch_colors.astype(np.float32)
    oklab_color = colour.Oklch_to_Oklab(oklch_color)
    xyz_color = colour.Oklab_to_XYZ(oklab_color)
    rgb_color = colour.XYZ_to_sRGB(xyz_color)
    rgb_color = np.clip(rgb_color * 255.0, 0, 255).astype(np.uint8)
    return rgb_color


class Gradient:
    def _get_colors(self, size: Size) -> np.ndarray: 
        # [W, H, 4]
        raise NotImplementedError()

    def _lerp_color(self, t: np.ndarray, mode) -> np.ndarray:
        if mode in 'RGB_OR_RGBA':
            colors = (1 - t[:, :, np.newaxis]) * np.array(self.c1) + t[:, :, np.newaxis] * np.array(self.c2)
            return np.clip(colors, 0, 255).astype(np.uint8)
        elif mode in 'OKLCH':
            l = self.c1[0] * (1 - t) + self.c2[0] * t
            c = self.c1[1] * (1 - t) + self.c2[1] * t
            h1, h2 = self.c1[2] / 360.0, self.c2[2] / 360.0
            if abs(h2 - h1) > 0.5:
                if h1 > h2:
                    h2 += 1.0
                else:
                    h1 += 1.0
            h = (h1 * (1 - t) + h2 * t) % 1.0 * 360.0
            return np.stack((l, c, h), axis=-1)
        else:
            raise ValueError(f"Invalid Gradient color mode: {mode}")

    def get_img(self, size: Size, mask: Image.Image=None, mode='RGB_OR_RGBA') -> Image.Image:
        img = Image.fromarray(self._get_colors(size, mode), 'RGBA')
        if mask:
            assert mask.size == size, "Mask size must match image size"
            if mask.mode == 'RGBA':
                mask = mask.getchannel('A')
            else:
                mask = mask.convert('L')
            img.putalpha(mask)
        return img

    def get_array(self, size: Size, mode='RGB_OR_RGBA') -> np.ndarray:
        return self._get_colors(size, mode)

class LinearGradient(Gradient):
    def __init__(self, c1: Color, c2: Color, p1: Position, p2: Position, method: str = 'seperate'):
        self.c1 = c1
        self.c2 = c2
        self.p1 = p1
        self.p2 = p2
        self.method = method
        assert p1 != p2, "p1 and p2 cannot be the same point"

    def _get_colors(self, size: Size, mode: str) -> np.ndarray:
        w, h = size
        pixel_p1 = np.array((self.p1[1] * h, self.p1[0] * w))
        pixel_p2 = np.array((self.p2[1] * h, self.p2[0] * w))
        y_indices, x_indices = np.meshgrid(np.arange(h), np.arange(w), indexing='ij')
        coords = np.stack((y_indices, x_indices), axis=-1) # (H, W, 2)
        if self.method == 'combine':
            gradient_vector = pixel_p2 - pixel_p1
            length_sq = np.sum(gradient_vector**2)
            vector_p1_to_pixel = coords - pixel_p1 # (H, W, 2)
            dot_product = np.sum(vector_p1_to_pixel * gradient_vector, axis=-1) # (H, W)
            t = dot_product / length_sq
        elif self.method == 'seperate': # seperate仅支持对角线/水平/垂直
            if abs(pixel_p1[0] - pixel_p2[0]) < 0.5:
                t = (coords[:, :, 1] - pixel_p1[1]) / (pixel_p2[1] - pixel_p1[1])
            elif abs(pixel_p1[1] - pixel_p2[1]) < 0.5:
                t = (coords[:, :, 0] - pixel_p1[0]) / (pixel_p2[0] - pixel_p1[0])
            else:
                vector_pixel_to_p1 = coords - pixel_p1
                vector_p2_to_p1 = pixel_p2 - pixel_p1
                t = np.average(vector_pixel_to_p1 / vector_p2_to_p1, axis=-1)
        else:
            raise ValueError(f"Invalid LinearGradient method: {self.method}")
        t_clamped = np.clip(t, 0, 1) 
        return self._lerp_color(t_clamped, mode)

@dataclass
class AdaptiveTextColor:
    pixelwise: bool = False
    light: Color = WHITE
    dark: Color = BLACK
    threshold: float = 0.4

ADAPTIVE_WB = AdaptiveTextColor()
ADAPTIVE_SHADOW = AdaptiveTextColor(
    light=(255, 255, 255, 100), 
    dark=(0, 0, 0, 100), 
)

# =========================== 绘图类 =========================== #

@dataclass
class PainterOperation:
    offset: Position
    size: Size
    func: Union[str, callable]
    args: List
    exclude_on_hash: bool

    def image_to_id(self, img_dict: Dict[int, Image.Image]):
        if isinstance(self.args, tuple):
            self.args = list(self.args)
        for i in range(len(self.args)):
            if isinstance(self.args[i], Image.Image):
                img_id = id(self.args[i])
                img_dict[img_id] = self.args[i]
                self.args[i] = f"%%image%%{img_id}"
    
    def id_to_image(self, img_dict: Dict[int, Image.Image]):
        if isinstance(self.args, tuple):
            self.args = list(self.args)
        for i in range(len(self.args)):
            if isinstance(self.args[i], str) and self.args[i].startswith("%%image%%"):
                img_id = int(self.args[i][9:])
                self.args[i] = img_dict[img_id]

class Painter:
    
    def __init__(self, img: Image.Image = None, size: Tuple[int, int] = None):
        self.operations: List[PainterOperation] = []
        if img is not None:
            self.img = img
            self.size = img.size
        elif size is not None:
            self.img = None
            self.size = size
        else:
            raise ValueError("Either img or size must be provided")
        self.offset = (0, 0)
        self.w = self.size[0]
        self.h = self.size[1]
        self.region_stack = []

    def _text(
        self, 
        text: str, 
        pos: Position, 
        font: Font,
        fill: Color = BLACK,
        align: str = "left"
    ):
        std_size = get_font_std_size(font)
        if not has_emoji(text):
            draw = ImageDraw.Draw(self.img)
            text_offset = (0, -std_size[1])
            pos = (pos[0] - text_offset[0] + self.offset[0], pos[1] - text_offset[1] + self.offset[1])
            draw.text(pos, text, font=font, fill=fill, align=align, anchor='ls')
        else:
            with Pilmoji(self.img, source=GoogleEmojiSource) as pilmoji:
                text_offset = (0, -std_size[1])
                offset = (0, 0) # 简化emoji offset处理
                scale = 1.0 # 简化scale处理
                offset = (int(offset[0] * std_size[1] / 32), int(offset[1] * std_size[1] / 32) - std_size[1])
                pos = (pos[0] - text_offset[0] + self.offset[0], pos[1] - text_offset[1] + self.offset[1])
                pilmoji.text(
                    pos, text, font=font, fill=fill, align=align, 
                    emoji_position_offset=offset, emoji_scale_factor=scale,
                    anchor='ls')
        return self
    
    def _get_aa_roundrect(
        self,
        size: Size, 
        fill: Color,
        radius: int, 
        stroke: Color=None, 
        stroke_width: int=1,
        corners = (True, True, True, True), 
        margin: int | tuple[int, int, int, int] = 0,    # left, top, right, bottom
    ) -> Image.Image:
        width, height = size
        if isinstance(margin, int):
            margin = (margin, margin, margin, margin)
        ml, mt, mr, mb = margin

        width, height = width - 1, height - 1
        radius = min(radius, width // 2, height // 2)
        realsize = (width + ml + mr + 1, height + mt + mb + 1)

        def getbox(x1, y1, x2, y2):
            return (x1 + ml, y1 + mt, x2 + ml, y2 + mt)
        def getpos(x, y):
            return (x + ml, y + mt)

        if radius <= 0:
            img = Image.new('RGBA', realsize, (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle(getbox(0, 0, width, height), fill=fill, outline=stroke, width=stroke_width)
            return img

        img = Image.new('RGBA', realsize, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        if fill:
            draw.rectangle(getbox(radius, 0, width - radius, height), fill=fill)
            draw.rectangle(getbox(0, radius, width, height - radius), fill=fill)

        if stroke and stroke_width > 0:
            draw.rectangle(getbox(radius, 0, width - radius, stroke_width), fill=stroke) # 上
            draw.rectangle(getbox(radius, height - stroke_width, width - radius, height), fill=stroke) # 下
            draw.rectangle(getbox(0, radius, stroke_width, height - radius), fill=stroke) # 左
            draw.rectangle(getbox(width - stroke_width, radius, width, height - radius), fill=stroke) # 右

        aa_scale = max(1, math.ceil(16 / radius))
        aa_radius = radius * aa_scale
        aa_stroke_width = stroke_width * aa_scale

        corner_aa = None
        if any(corners):
            corner_canvas = Image.new('RGBA', (aa_radius * 2, aa_radius * 2), (0, 0, 0, 0))
            corner_draw = ImageDraw.Draw(corner_canvas)
            corner_draw.rounded_rectangle(
                (0, 0, aa_radius * 2, aa_radius * 2),
                radius=aa_radius,
                fill=fill,
                outline=stroke,
                width=aa_stroke_width,
                corners=(True, True, True, True)
            )
            corner_canvas = corner_canvas.crop((0, 0, aa_radius, aa_radius))
            corner_aa = corner_canvas.resize((radius + 1, radius + 1), Image.Resampling.BICUBIC)
        
        sharp_corner = None
        if not all(corners):
            sharp_corner = Image.new('RGBA', (radius + 1, radius + 1), (0, 0, 0, 0))
            sharp_draw = ImageDraw.Draw(sharp_corner)
            if fill:
                sharp_draw.rectangle((0, 0, radius + 1, radius + 1), fill=fill)
            if stroke and stroke_width > 0:
                sharp_draw.rectangle((0, 0, radius + 1, stroke_width), fill=stroke) # 上
                sharp_draw.rectangle((0, 0, stroke_width, radius + 1), fill=stroke) # 左

        tl, tr, br, bl = corners
        corner = corner_aa if tl else sharp_corner
        img.paste(corner, getpos(0, 0))
        corner = (corner_aa if tr else sharp_corner).transpose(Image.FLIP_LEFT_RIGHT)
        img.paste(corner, getpos(width - radius, 0))
        corner = (corner_aa if br else sharp_corner).transpose(Image.ROTATE_180)
        img.paste(corner, getpos(width - radius, height - radius))
        corner = (corner_aa if bl else sharp_corner).transpose(Image.FLIP_TOP_BOTTOM)
        img.paste(corner, getpos(0, height - radius))
        return img

    @staticmethod
    def _execute(operations: List[PainterOperation], img: Image.Image, size: Tuple[int, int], image_dict: Dict[str, Image.Image]) -> Image.Image:
        if img is None:
            img = Image.new('RGBA', size, TRANSPARENT)
        p = Painter(img, size)
        for op in operations:
            op.id_to_image(image_dict)
            p.offset = op.offset
            p.size = op.size
            p.w, p.h = op.size
            func = getattr(p, op.func) if isinstance(op.func, str) else op.func
            kwargs = {}
            for key, value in get_type_hints(func).items():
                if value == Painter:
                    kwargs[key] = p
            func(*op.args, **kwargs)
        return p.img

    async def get(self, cache_key: str=None) -> Image.Image:
        if cache_key is not None:
            op_hash = await asyncio.to_thread(deterministic_hash, {"key": cache_key, "op": self.operations})
            paths = glob.glob(os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__*.png"))
            if paths:
                path = paths[0]
                if path.endswith(f"{cache_key}__{op_hash}.png"):
                    img = Image.open(path)
                    img.load()
                    return img
                else:
                    for p in paths:
                        try: os.remove(p)
                        except Exception: pass

        image_dict = {}
        for op in self.operations:
            op.image_to_id(image_dict)
            
        # 使用线程池执行绘图操作
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            self.img = await loop.run_in_executor(pool, Painter._execute, self.operations, self.img, self.size, image_dict)

        self.operations = []

        if cache_key is not None:
            try:
                cache_path = os.path.join(PAINTER_CACHE_DIR, f"{cache_key}__{op_hash}.png")
                os.makedirs(PAINTER_CACHE_DIR, exist_ok=True)
                self.img.save(cache_path, format='PNG')
            except:
                pass

        return self.img
    
    def add_operation(self, func: Union[str, callable], exclude_on_hash: bool, args: List[Any]):
        self.operations.append(PainterOperation(
            offset=self.offset,
            size=self.size,
            func=func,
            args=list(args),
            exclude_on_hash=exclude_on_hash,
        ))
        return self

    def set_region(self, pos: Position, size: Size):
        assert isinstance(pos[0], int) and isinstance(pos[1], int), "Position must be integer"
        assert isinstance(size[0], int) and isinstance(size[1], int), "Size must be integer"
        self.region_stack.append((self.offset, self.size))
        self.offset = pos
        self.size = size
        self.w = size[0]
        self.h = size[1]
        return self

    def move_region(self, offset: Position, size: Size = None):
        if size is None:
            size = (self.w, self.h)
        new_pos = (self.offset[0] + offset[0], self.offset[1] + offset[1])
        return self.set_region(new_pos, size)

    def shrink_region(self, margin: Tuple[int, int]):
        mx, my = margin
        new_pos = (self.offset[0] + mx, self.offset[1] + my)
        new_size = (self.w - mx * 2, self.h - my * 2)
        return self.set_region(new_pos, new_size)

    def restore_region(self, depth=1):
        if not self.region_stack:
            self.offset = (0, 0)
            self.size = self.img.size
            self.w = self.img.size[0]
            self.h = self.img.size[1]
        else:
            self.offset, self.size = self.region_stack.pop()
            self.w = self.size[0]
            self.h = self.size[1]
        if depth > 1:
            return self.restore_region(depth - 1)
        return self

    def text(
        self, 
        text: str, 
        pos: Position, 
        font: Union[FontDesc, Font],
        fill: Union[Color, LinearGradient, AdaptiveTextColor] = BLACK,
        align: str = "left",
        exclude_on_hash: bool = False,
    ):
        return self.add_operation("_impl_text", exclude_on_hash, (text, pos, font, fill, align))
        
    def paste(
        self, 
        sub_img: Image.Image,
        pos: Position, 
        size: Size = None,
        use_shadow: bool = False,
        shadow_width: int = 8,
        shadow_alpha: float = 0.6,
        exclude_on_hash: bool = False,
    ) -> Image.Image:
        return self.add_operation("_impl_paste", exclude_on_hash, (sub_img, pos, size, use_shadow, shadow_width, shadow_alpha))

    def paste_with_alphablend(
        self, 
        sub_img: Image.Image,
        pos: Position, 
        size: Size = None,
        alpha: float = None,
        use_shadow: bool = False,
        shadow_width: int = 8,
        shadow_alpha: float = 0.6,
        exclude_on_hash: bool = False,
    ) -> Image.Image:
        return self.add_operation("_impl_paste_with_alphablend", exclude_on_hash, (sub_img, pos, size, alpha, use_shadow, shadow_width, shadow_alpha))

    def rect(
        self, 
        pos: Position, 
        size: Size, 
        fill: Union[Color, Gradient], 
        stroke: Color=None, 
        stroke_width: int=1,
        exclude_on_hash: bool = False
    ):
        return self.add_operation("_impl_rect", exclude_on_hash, (pos, size, fill, stroke, stroke_width))
        
    def roundrect(
        self, 
        pos: Position, 
        size: Size, 
        fill: Union[Color, Gradient],
        radius: int, 
        stroke: Color=None, 
        stroke_width: int=1,
        corners = (True, True, True, True),
        exclude_on_hash: bool = False
    ):
        return self.add_operation("_impl_roundrect", exclude_on_hash, (pos, size, fill, radius, stroke, stroke_width, corners))

    def _impl_text(
        self, 
        text: str, 
        pos: Position, 
        font: Union[FontDesc, Font],
        fill: Union[Color, LinearGradient, AdaptiveTextColor] = BLACK,
        align: str = "left"
    ):
        def adjust_overlay_alpha_by_color(overlay: Image.Image, color: Color):
            if len(color) < 4 or color[3] == 255:
                return
            overlay_alpha = overlay.getchannel('A')
            overlay_alpha = Image.eval(overlay_alpha, lambda a: int(a * color[3] / 255))
            overlay.putalpha(overlay_alpha)

        if isinstance(font, FontDesc):
            font = get_font(font.path, font.size)

        if isinstance(fill, LinearGradient):
            gradient = fill
            adaptive = None
            fill = BLACK
        elif isinstance(fill, AdaptiveTextColor):
            gradient = None
            adaptive = fill
            fill = fill.light[:3]
        else:
            gradient = None
            adaptive = None

        if (len(fill) == 3 or fill[3] == 255) and not gradient and not adaptive:
            self._text(text, pos, font, fill, align)
        else:
            text_size = get_text_size(font, text)
            overlay_size = (text_size[0] + 10, text_size[1] + 10)
            overlay = Image.new('RGBA', overlay_size, (0, 0, 0, 0))
            p = Painter(overlay)
            p._text(text, (0, 0), font, fill=fill, align=align)

            if gradient:
                gradient_img = gradient.get_img(overlay_size, overlay)
                overlay = gradient_img

            elif adaptive:
                dark_overlay = Image.new('RGBA', overlay_size, (0, 0, 0, 0))
                dark_p = Painter(dark_overlay)
                dark_p._text(text, (0, 0), font, fill=adaptive.dark[:3], align=align)

                adjust_overlay_alpha_by_color(overlay, adaptive.light)
                adjust_overlay_alpha_by_color(dark_overlay, adaptive.dark)

                bg_img = self.img.crop((
                    pos[0] + self.offset[0], 
                    pos[1] + self.offset[1], 
                    pos[0] + self.offset[0] + overlay_size[0], 
                    pos[1] + self.offset[1] + overlay_size[1]
                ))

                if adaptive.pixelwise:
                    gray = bg_img.filter(ImageFilter.BoxBlur(radius=8)).convert('L')
                else:
                    avg_color = np.array(bg_img).reshape(-1, 4).mean(axis=0)
                    gray = Image.new('RGB', bg_img.size, tuple(avg_color[:3].astype(int))).convert('L')

                threshold = int(adaptive.threshold * 255)
                mask = gray.point(lambda p: 255 if p > threshold else 0, 'L')
                overlay.paste(dark_overlay, (0, 0), mask)

            elif fill[3] < 255:
                adjust_overlay_alpha_by_color(overlay, fill)

            self.img.alpha_composite(overlay, (pos[0] + self.offset[0], pos[1] + self.offset[1]))

        return self
        
    def _impl_paste(
        self, 
        sub_img: Image.Image,
        pos: Position, 
        size: Size = None,
        use_shadow: bool = False,
        shadow_width: int = 6,
        shadow_alpha: float = 0.6,
    ) -> Image.Image:
        if size and size != sub_img.size:
            sub_img = sub_img.resize(size)
        if sub_img.mode not in ('RGB', 'RGBA'):
            sub_img = sub_img.convert('RGBA')

        if use_shadow:
            w, h = sub_img.size
            sw = shadow_width
            lw, lh = w + sw * 2, h + sw * 2
            shadow_mask = Image.new('L', (lw, lh), 0)
            shadow_mask.paste(Image.new('L', sub_img.size, int(255 * shadow_alpha)), (sw, sw), sub_img)
            blurred_shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=sw // 2))
            inner_mask = ImageChops.invert(shadow_mask)
            blurred_shadow_mask = ImageChops.multiply(blurred_shadow_mask, inner_mask)
            shadow = Image.new('RGBA', (lw, lh), (0, 0, 0, 255))
            shadow.putalpha(blurred_shadow_mask)
            self.img.alpha_composite(shadow, (pos[0] + self.offset[0] - sw, pos[1] + self.offset[1] - sw))

        if sub_img.mode == 'RGBA':
            self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]), sub_img)
        else:
            self.img.paste(sub_img, (pos[0] + self.offset[0], pos[1] + self.offset[1]))
        return self

    def _impl_paste_with_alphablend(
        self, 
        sub_img: Image.Image,
        pos: Position, 
        size: Size = None,
        alpha: float = None,
        use_shadow: bool = False,
        shadow_width: int = 6,
        shadow_alpha: float = 0.6,
    ) -> Image.Image:
        if size and size != sub_img.size:
            sub_img = sub_img.resize(size)
        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        overlay = Image.new('RGBA', sub_img.size, (0, 0, 0, 0))
        overlay.paste(sub_img, (0, 0))
        if alpha is not None:
            overlay_alpha = overlay.getchannel('A')
            overlay_alpha = Image.eval(overlay_alpha, lambda a: int(a * alpha))
            overlay.putalpha(overlay_alpha)

        if use_shadow:
            w, h = overlay.size
            sw = shadow_width
            lw, lh = w + sw * 2, h + sw * 2
            shadow_mask = Image.new('L', (lw, lh), 0)
            shadow_mask.paste(Image.new('L', overlay.size, int(255 * shadow_alpha)), (sw, sw), overlay)
            blurred_shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=sw // 2))
            inner_mask = ImageChops.invert(shadow_mask)
            blurred_shadow_mask = ImageChops.multiply(blurred_shadow_mask, inner_mask)
            shadow = Image.new('RGBA', (lw, lh), (0, 0, 0, 255))
            shadow.putalpha(blurred_shadow_mask)
            self.img.alpha_composite(shadow, (pos[0] - sw, pos[1] - sw))

        self.img.alpha_composite(overlay, pos)
        return self

    def _impl_rect(
        self, 
        pos: Position, 
        size: Size, 
        fill: Union[Color, Gradient], 
        stroke: Color=None, 
        stroke_width: int=1,
    ):
        if min(size) <= 0:
            return self

        if isinstance(fill, Gradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None
        
        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])
        bbox = pos + (pos[0] + size[0], pos[1] + size[1])

        if fill[3] == 255 and not gradient:
            draw = ImageDraw.Draw(self.img)
            draw.rectangle(bbox, fill=fill, outline=stroke, width=stroke_width)
        else:
            overlay_size = (size[0] + 1, size[1] + 1)
            overlay = Image.new('RGBA', overlay_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            draw.rectangle((0, 0, size[0], size[1]), fill=fill, outline=stroke, width=stroke_width)
            if gradient:
                gradient_img = gradient.get_img(overlay_size, overlay)
                overlay = gradient_img
            self.img.alpha_composite(overlay, (pos[0], pos[1]))

        return self
        
    def _impl_roundrect(
        self, 
        pos: Position, 
        size: Size, 
        fill: Union[Color, Gradient],
        radius: int, 
        stroke: Color=None, 
        stroke_width: int=1,
        corners = (True, True, True, True),
    ):
        if min(size) <= 0:
            return self

        if isinstance(fill, Gradient):
            gradient = fill
            fill = BLACK
        else:
            gradient = None

        pos = (pos[0] + self.offset[0], pos[1] + self.offset[1])

        overlay = self._get_aa_roundrect(size, fill, radius, stroke, stroke_width, corners)

        if gradient:
            gradient_img = gradient.get_img(overlay.size, overlay)
            overlay = gradient_img

        self.img.alpha_composite(overlay, (pos[0], pos[1]))
        
        return self
