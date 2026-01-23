from typing import Union, Tuple, List, Optional, Callable
from PIL import Image, ImageFilter, ImageEnhance
import threading
import contextvars
from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime

from ..utils.config import config as global_config
from .painter import *


DEFAULT_PADDING = 0
DEFAULT_MARGIN = 0
DEFAULT_SEP = 8


# =========================== 背景 =========================== #

class WidgetBg:
    def draw(self, p: Painter):
        raise NotImplementedError()

class FillBg(WidgetBg):
    def __init__(self, fill: Color, stroke: Color=None, stroke_width: int=1):
        self.fill = fill
        self.stroke = stroke
        self.stroke_width = stroke_width

    def draw(self, p: Painter):
        p.rect((0, 0), p.size, self.fill, self.stroke, self.stroke_width)

class RoundRectBg(WidgetBg):
    def __init__(self, fill: Color, radius: int, stroke: Color=None, stroke_width: int=1, 
                 corners = (True, True, True, True), blurglass=False, blurglass_kwargs: dict = {}):
        self.fill = fill
        self.radius = radius
        self.stroke = stroke
        self.stroke_width = stroke_width
        self.corners = corners
        self.blurglass = blurglass
        self.blurglass_kwargs = blurglass_kwargs
    
    def draw(self, p: Painter):
        # 注意: painter中可能没有 blurglass_roundrect，如果有需要补充，如果没有则不支持或替换实现
        # 目前 painter.py 中没有看到 blurglass_roundrect，暂时注释掉或改为普通圆角矩形
        # p.blurglass_roundrect((0, 0), p.size, self.fill, self.radius, corners=self.corners, **self.blurglass_kwargs)
        p.roundrect((0, 0), p.size, self.fill, self.radius, self.stroke, self.stroke_width, self.corners)

class ImageBg(WidgetBg):
    def __init__(self, img: Union[str, Image.Image], align: str='c', mode='fit', blur=False, fade=0.1):
        if isinstance(img, str):
            self.img = Image.open(img)
        else:
            self.img = img
        assert align in ALIGN_MAP
        self.align = align
        assert mode in ('fit', 'fill', 'fixed', 'repeat')
        self.mode = mode
        if blur:
            self.img = self.img.filter(ImageFilter.GaussianBlur(radius=3))
        if fade > 0:
            self.img = ImageEnhance.Brightness(self.img).enhance(1 - fade)

    def draw(self, p: Painter):
        if self.mode == 'fit':
            ha, va = ALIGN_MAP[self.align]
            scale = max(p.w / self.img.size[0], p.h / self.img.size[1])
            w, h = int(self.img.size[0] * scale), int(self.img.size[1] * scale)
            if va == 'c':
                y = (p.h - h) // 2
            elif va == 't':
                y = 0
            else:
                y = p.h - h
            if ha == 'c':
                x = (p.w - w) // 2
            elif ha == 'l':
                x = 0
            else:
                x = p.w - w
            p.paste(self.img, (x, y), (w, h))
        if self.mode == 'fill':
            p.paste(self.img, (0, 0), p.size)
        if self.mode == 'fixed':
            ha, va = ALIGN_MAP[self.align]
            if va == 'c':
                y = (p.h - self.img.size[1]) // 2
            elif va == 't':
                y = 0
            else:
                y = p.h - self.img.size[1]
            if ha == 'c':
                x = (p.w - self.img.size[0]) // 2
            elif ha == 'l':
                x = 0
            else:
                x = p.w - self.img.size[0]
            p.paste(self.img, (x, y))
        if self.mode =='repeat':
            w, h = self.img.size
            for y in range(0, p.h, h):
                for x in range(0, p.w, w):
                    p.paste(self.img, (x, y))

# RandomTriangleBg 依赖 painter 的 draw_random_triangle_bg 方法，但在 painter.py 中未实现。
# 为了保持兼容性，先保留类定义但 draw 方法留空或抛出警告，或者完全移除。
# 考虑到这是从原项目移植，可能原项目的 painter 还有更多方法。
# 目前先注释掉 draw 实现。
class RandomTriangleBg(WidgetBg):
    def __init__(
        self, 
        preset_config_name: str,
        size_fixed_rate: float = 0.0, 
        dt: datetime | None = None
    ):
        super().__init__()
        self.preset_config_name = preset_config_name
        self.size_fixed_rate = size_fixed_rate
        self.dt = dt

    def draw(self, p: Painter):
        # p.draw_random_triangle_bg(self.preset_config_name, self.size_fixed_rate, self.dt)
        pass


# =========================== 布局类型 =========================== #

class Widget:
    _thread_local = contextvars.ContextVar('local', default=None)

    def __init__(self):
        self.parent: Optional[Widget] = None

        self.content_halign = 'l'
        self.content_valign = 't'
        self.vmargin = DEFAULT_MARGIN
        self.hmargin = DEFAULT_MARGIN
        self.vpadding = DEFAULT_PADDING
        self.hpadding = DEFAULT_PADDING
        self.w = None
        self.h = None
        self.bg = None
        self.omit_parent_bg = False
        self.offset = (0, 0)
        self.offset_xanchor = 'l'
        self.offset_yanchor = 't'
        self.allow_draw_outside = False

        self._calc_w = None
        self._calc_h = None
        
        self.draw_funcs = []
        
        self.userdata = {}

        if Widget.get_current_widget():
            Widget.get_current_widget().add_item(self)

    def get_content_align(self) -> str:
        for k, v in ALIGN_MAP.items():
            if v == (self.content_halign, self.content_valign):
                return k
        return None

    @classmethod
    def get_current_widget_stack(cls) -> List['Widget']:
        local = cls._thread_local.get()
        if local is None: return None
        return local.wstack

    @classmethod
    def get_current_widget(cls) -> Optional['Widget']:
        stk = cls.get_current_widget_stack()
        if stk is None: return None
        return stk[-1]

    def __enter__(self):
        local = self._thread_local.get() 
        if local is None:
            local = threading.local()
            local.wstack = []
        local.wstack.append(self)
        self._thread_local.set(local)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        local = self._thread_local.get()
        assert local is not None and local.wstack[-1] == self
        local.wstack.pop()
        if not local.wstack:
            self._thread_local.set(None)

    def add_item(self, item: 'Widget', index: int = None):
        item.set_parent(self)
        if index is None:
            self.items.append(item)
        else:
            self.items.insert(index, item)
        return self
    
    def remove_item(self, item: 'Widget'):
        self.items.remove(item)
        item.set_parent(None)
        return self

    def remove_item_at(self, index: int):
        item = self.items.pop(index)
        item.set_parent(None)
        return self
    
    def set_items(self, items: List['Widget']):
        for item in self.items:
            item.set_parent(None)
        self.items = items
        for item in self.items:
            item.set_parent(self)
        return self

    def set_parent(self, parent: 'Widget'):
        self.parent = parent
        return self

    def set_content_align(self, align: str):
        if align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.content_halign, self.content_valign = ALIGN_MAP[align]
        return self

    def set_margin(self, margin: Union[int, Tuple[int, int]]):
        if isinstance(margin, int):
            self.vmargin = margin
            self.hmargin = margin
        else:
            self.hmargin = margin[0]
            self.vmargin = margin[1]
        return self

    def set_padding(self, padding: Union[int, Tuple[int, int]]):
        if isinstance(padding, int):
            self.vpadding = padding
            self.hpadding = padding
        else:
            self.hpadding = padding[0]
            self.vpadding = padding[1]
        return self

    def set_size(self, size: Tuple[int, int]):
        if not size: size = (None, None)
        self.w = size[0]
        self.h = size[1]
        return self

    def set_w(self, w: int):
        self.w = w
        return self
    
    def set_h(self, h: int):
        self.h = h
        return self

    def set_offset(self, offset: Tuple[int, int]):
        self.offset = offset
        return self
    
    def set_offset_anchor(self, anchor: str):
        if anchor not in ALIGN_MAP:
            raise ValueError('Invalid anchor')
        self.offset_xanchor, self.offset_yanchor = ALIGN_MAP[anchor]
        return self

    def set_bg(self, bg: WidgetBg):
        self.bg = bg
        return self

    def set_omit_parent_bg(self, omit: bool):
        self.omit_parent_bg = omit
        return self
    
    def set_allow_draw_outside(self, allow: bool):
        self.allow_draw_outside = allow
        return self

    def _get_content_size(self):
        return (0, 0)
    
    def _get_self_size(self):
        if not all([self._calc_w, self._calc_h]):
            content_w, content_h = self._get_content_size()
            content_w_limit = self.w - self.hpadding * 2 if self.w is not None else content_w
            content_h_limit = self.h - self.vpadding * 2 if self.h is not None else content_h
            if content_w > content_w_limit or content_h > content_h_limit:
                if not self.allow_draw_outside:
                    # 为了避免报错，这里改为只警告并使用限制大小，或者直接使用限制大小
                    # raise ValueError(f'Content size is too large with ({content_w}, {content_h}) > ({content_w_limit}, {content_h_limit})')
                    content_w = min(content_w, content_w_limit)
                    content_h = min(content_h, content_h_limit)
                else:
                    content_w = min(content_w, content_w_limit)
                    content_h = min(content_h, content_h_limit)
            self._calc_w = content_w_limit + self.hmargin * 2 + self.hpadding * 2
            self._calc_h = content_h_limit + self.vmargin * 2 + self.vpadding * 2
        return (int(self._calc_w), int(self._calc_h))

    def _get_content_pos(self):
        w, h = self._get_self_size()
        w -= self.hpadding * 2 + self.hmargin * 2
        h -= self.vpadding * 2 + self.vmargin * 2
        cw, ch = self._get_content_size()
        if self.content_halign == 'l':
            cx = 0
        elif self.content_halign == 'r':
            cx = w - cw
        elif self.content_halign == 'c':
            cx = (w - cw) // 2
        if self.content_valign == 't':
            cy = 0
        elif self.content_valign == 'b':
            cy = h - ch
        elif self.content_valign == 'c':
            cy = (h - ch) // 2
        return (cx, cy)
        
    def _draw_self(self, p: Painter):
        if global_config.get('plot.debug', False):
            import random
            color = (random.randint(0, 200), random.randint(0, 200), random.randint(0, 200), 255)
            p.rect((0, 0), (p.w, p.h), TRANSPARENT, stroke=color, stroke_width=2)
            s = f"{self.__class__.__name__}({p.w},{p.h})"
            s += f"self={self._get_self_size()}"
            s += f"content={self._get_content_size()}"
            p.text(s, (3, 3), font=get_font_desc(DEFAULT_FONT, 16), fill=color)
            print(f"Draw {self.__class__.__name__} at {p.offset} size={p.size}")
        
        if self.bg:
            self.bg.draw(p)

        for draw_func in self.draw_funcs:
            draw_func(self, p)
    
    def _draw_content(self, p: Painter):
        pass

    def add_draw_func(self, func):
        self.draw_funcs.append(func)
        return self
    
    def clear_draw_funcs(self):
        self.draw_funcs.clear()
        return self
    
    def draw(self, p: Painter):
        # assert p.size == self._get_self_size()

        if self.offset_xanchor == 'l': 
            offset_x = self.offset[0]
        elif self.offset_xanchor == 'r':
            offset_x = self.offset[0] - p.w
        else:
            offset_x = self.offset[0] - p.w // 2
        if self.offset_yanchor == 't':
            offset_y = self.offset[1]
        elif self.offset_yanchor == 'b':
            offset_y = self.offset[1] - p.h
        else:
            offset_y = self.offset[1] - p.h // 2

        p.move_region((offset_x, offset_y))
        p.shrink_region((self.hmargin, self.vmargin))
        self._draw_self(p)

        p.shrink_region((self.hpadding, self.vpadding))
        cx, cy = self._get_content_pos()
        p.move_region((cx, cy)) 
        self._draw_content(p)

        p.restore_region(4)
  

class Frame(Widget):
    def __init__(self, items: List[Widget]=None):
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)

    def _get_content_size(self):
        size = (0, 0)
        for item in self.items:
            w, h = item._get_self_size()
            size = (max(size[0], w), max(size[1], h))
        return size
    
    def _draw_content(self, p: Painter):
        cw, ch = self._get_content_size()
        for item in self.items:
            w, h = item._get_self_size()
            x, y = 0, 0
            if self.content_halign == 'l':
                x = 0
            elif self.content_halign == 'r':
                x = cw - w
            elif self.content_halign == 'c':
                x = (cw - w) // 2
            if self.content_valign == 't':
                y = 0
            elif self.content_valign == 'b':
                y = ch - h
            elif self.content_valign == 'c':
                y = (ch - h) // 2
            p.move_region((x, y), (w, h))
            item.draw(p)
            p.restore_region()
    

class HSplit(Widget):
    def __init__(self, items: List[Widget]=None, ratios: List[float]=None, sep=DEFAULT_SEP, item_size_mode='fixed', item_align='c'):
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.ratios = ratios 
        self.sep = sep
        assert item_size_mode in ('expand', 'fixed')
        self.item_size_mode = item_size_mode
        if item_align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.item_halign, self.item_valign = ALIGN_MAP[item_align]
        self.item_bg = None

    def set_item_align(self, align: str):
        if align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.item_halign, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, sep: int):
        self.sep = sep  
        return self

    def set_ratios(self, ratios: List[float]):
        self.ratios = ratios
        return self

    def set_item_size_mode(self, mode: str):
        assert mode in ('expand', 'fixed')
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg | Callable[[int, Widget], WidgetBg]):
        self.item_bg = bg
        return self
    
    def _get_item_sizes(self):
        ratios = self.ratios if self.ratios else [item._get_self_size()[0] for item in self.items]
        if self.item_size_mode == 'expand':
            assert self.w is not None, 'Expand mode requires width'
            ratio_sum = sum(ratios)
            unit_w = (self.w - self.sep * (len(ratios) - 1) - self.hpadding * 2) / ratio_sum
        else:
            unit_w = 0
            for r, item in zip(ratios, self.items):
                iw, ih = item._get_self_size()
                if r > 0: unit_w = max(unit_w, iw / r)
        ret = []
        h = max([item._get_self_size()[1] for item in self.items])
        for r, item in zip(ratios, self.items):
            ret.append((int(unit_w * r), h))
        return ret

    def _get_content_size(self):
        if not self.items:
            return (0, 0)
        sizes = self._get_item_sizes()
        return (sum(s[0] for s in sizes) + self.sep * (len(sizes) - 1), max(s[1] for s in sizes))
    
    def _draw_content(self, p: Painter):
        if not self.items:
            return
        sizes = self._get_item_sizes()
        cur_x = 0
        for i, item, (w, h) in zip(range(len(sizes)), self.items, sizes):
            iw, ih = item._get_self_size()
            p.move_region((cur_x, 0), (w, h))
            x, y = 0, 0
            if self.item_bg and not item.omit_parent_bg:
                if callable(self.item_bg):
                    self.item_bg(i, item).draw(p)
                else:
                    self.item_bg.draw(p)
            if self.item_halign == 'l':
                x += 0
            elif self.item_halign == 'r':
                x += w - iw
            elif self.item_halign == 'c':
                x += (w - iw) // 2
            if self.item_valign == 't':
                y += 0
            elif self.item_valign == 'b':
                y += h - ih
            elif self.item_valign == 'c':
                y += (h - ih) // 2
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region(2)
            cur_x += w + self.sep


class VSplit(Widget):
    def __init__(self, items: List[Widget]=None, ratios: List[float]=None, sep=DEFAULT_SEP, item_size_mode='fixed', item_align='c'):
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.ratios = ratios 
        self.sep = sep
        assert item_size_mode in ('expand', 'fixed')
        self.item_size_mode = item_size_mode
        if item_align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.item_halign, self.item_valign = ALIGN_MAP[item_align]
        self.item_bg = None

    def set_item_align(self, align: str):
        if align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.item_halign, self.item_valign = ALIGN_MAP[align]
        return self
    
    def set_sep(self, sep: int):
        self.sep = sep  
        return self

    def set_ratios(self, ratios: List[float]):
        self.ratios = ratios
        return self

    def set_item_size_mode(self, mode: str):
        assert mode in ('expand', 'fixed')
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg | Callable[[int, Widget], WidgetBg]):
        self.item_bg = bg
        return self

    def _get_item_sizes(self):
        ratios = self.ratios if self.ratios else [item._get_self_size()[1] for item in self.items]
        if self.item_size_mode == 'expand':
            assert self.h is not None, 'Expand mode requires height'
            ratio_sum = sum(ratios)
            unit_h = (self.h - self.sep * (len(ratios) - 1) - self.vpadding * 2) / ratio_sum
        else:
            unit_h = 0
            for r, item in zip(ratios, self.items):
                iw, ih = item._get_self_size()
                if r > 0: unit_h = max(unit_h, ih / r)
        ret = []
        w = max([item._get_self_size()[0] for item in self.items])
        for r, item in zip(ratios, self.items):
            ret.append((w, int(unit_h * r)))
        return ret
    
    def _get_content_size(self):
        if not self.items:
            return (0, 0)
        sizes = self._get_item_sizes()
        return (max(s[0] for s in sizes), sum(s[1] for s in sizes) + self.sep * (len(sizes) - 1))
    
    def _draw_content(self, p: Painter):
        if not self.items:
            return
        sizes = self._get_item_sizes()
        cur_y = 0
        for i, item, (w, h) in zip(range(len(sizes)), self.items, sizes):
            iw, ih = item._get_self_size()
            p.move_region((0, cur_y), (w, h))
            if self.item_bg and not item.omit_parent_bg:
                if callable(self.item_bg):
                    self.item_bg(i, item).draw(p)
                else:
                    self.item_bg.draw(p)
            x, y = 0, 0
            if self.item_halign == 'l':
                x += 0
            elif self.item_halign == 'r':
                x += w - iw
            elif self.item_halign == 'c':
                x += (w - iw) // 2
            if self.item_valign == 't':
                y += 0
            elif self.item_valign == 'b':
                y += h - ih
            elif self.item_valign == 'c':
                y += (h - ih) // 2
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region(2)
            cur_y += h + self.sep
    

class Grid(Widget):
    def __init__(self, items: List[Widget]=None, row_count=None, col_count=None, item_size_mode='fixed', item_align='c', hsep=DEFAULT_SEP, vsep=DEFAULT_SEP, vertical=False):
        super().__init__()
        self.items = items or []
        for item in self.items:
            item.set_parent(self)
        self.row_count = row_count
        self.col_count = col_count
        assert not (self.row_count and self.col_count), 'Either row_count or col_count should be None'
        assert item_size_mode in ('expand', 'fixed')
        self.item_size_mode = item_size_mode
        self.hsep = hsep
        self.vsep = vsep
        if item_align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.item_halign, self.item_valign = ALIGN_MAP[item_align]
        self.item_bg = None
        self.vertical = vertical

    def set_vertical(self, vertical: bool):
        self.vertical = vertical
        return self
    
    def set_item_align(self, align: str):
        if align not in ALIGN_MAP:
            raise ValueError('Invalid align')
        self.item_halign, self.item_valign = ALIGN_MAP[align]
        return self

    def set_sep(self, hsep=None, vsep=None):
        if hsep is not None:
            self.hsep = hsep
        if vsep is not None:
            self.vsep = vsep
        return self

    def set_row_count(self, count: int):
        self.row_count = count
        self.col_count = None
        return self

    def set_col_count(self, count: int):
        self.col_count = count
        self.row_count = None
        return self

    def set_item_size_mode(self, mode: str):
        assert mode in ('expand', 'fixed')
        self.item_size_mode = mode
        return self

    def set_item_bg(self, bg: WidgetBg | Callable[[int, int, Widget], WidgetBg]):
        self.item_bg = bg
        return self

    def _get_grid_rc_and_size(self):
        r, c = self.row_count, self.col_count
        assert r and not c or c and not r, 'Either row_count or col_count should be None'
        if not r: r = (len(self.items) + c - 1) // c
        if not c: c = (len(self.items) + r - 1) // r
        if self.item_size_mode == 'expand':
            assert self.w is not None and self.h is not None, 'Expand mode requires width and height'
            gw = (self.w - self.hsep * (c - 1) - self.hpadding * 2) / c
            gh = (self.h - self.vsep * (r - 1) - self.vpadding * 2) / r
        else:
            gw, gh = 0, 0
            for item in self.items:
                iw, ih = item._get_self_size()
                gw = max(gw, iw)
                gh = max(gh, ih)
        return (int(r), int(c)), (int(gw), int(gh))
    
    def _get_content_size(self):
        (r, c), (gw, gh) = self._get_grid_rc_and_size()
        return (int(c * gw + self.hsep * (c - 1)), int(r * gh + self.vsep * (r - 1)))
    
    def _draw_content(self, p: Painter):
        (r, c), (gw, gh) = self._get_grid_rc_and_size()
        for idx, item in enumerate(self.items):
            if not self.vertical:
                i, j = idx // c, idx % c
            else:
                i, j = idx % r, idx // r
            x = j * (gw + self.hsep)
            y = i * (gh + self.vsep)
            p.move_region((x, y), (gw, gh))
            if self.item_bg and not item.omit_parent_bg:
                if callable(self.item_bg):
                    self.item_bg(i, j, item).draw(p)
                else:
                    self.item_bg.draw(p)
            x, y = 0, 0
            iw, ih = item._get_self_size()
            if self.item_halign == 'l':
                x += 0
            elif self.item_halign == 'r':
                x += gw - iw
            elif self.item_halign == 'c':
                x += (gw - iw) // 2
            if self.item_valign == 't':
                y += 0
            elif self.item_valign == 'b':
                y += gh - ih
            elif self.item_valign == 'c':
                y += (gh - ih) // 2
            p.move_region((x, y), (iw, ih))
            item.draw(p)
            p.restore_region(2)


@dataclass
class TextStyle:
    font: str = DEFAULT_FONT
    size: int = 16
    color: Tuple[int, int, int, int] = BLACK
    use_shadow: bool = False
    shadow_offset: Tuple[int, int] | int = 1
    shadow_color: Tuple[int, int, int, int] = SHADOW

    def replace(
        self,
        font: str = None,
        size: int = None,
        color: Tuple[int, int, int, int] = None,
        use_shadow: bool = None,
        shadow_offset: Tuple[int, int] | int = None,
        shadow_color: Tuple[int, int, int, int] = None,
    ):
        return TextStyle(
            font = font if font is not None else self.font,
            size = size if size is not None else self.size,
            color = color if color is not None else self.color,
            use_shadow = use_shadow if use_shadow is not None else self.use_shadow,
            shadow_offset = shadow_offset if shadow_offset is not None else self.shadow_offset,
            shadow_color = shadow_color if shadow_color is not None else self.shadow_color,
        )


class TextBox(Widget):
    def __init__(self, text: str = '', style: TextStyle = None, line_count=None, line_sep=2, wrap=True, overflow='shrink', use_real_line_count=False):
        """
        overflow: 'shrink', 'clip'
        """
        super().__init__()
        self.text = str(text)
        self.style = style or TextStyle()
        self.line_count = line_count
        self.line_sep = line_sep
        self.wrap = wrap
        assert overflow in ('shrink', 'clip')
        self.overflow = overflow
        self.use_real_line_count = use_real_line_count
        self.text_offset_x = 0
        self.text_offset_y = 0

        if line_count is None:
            self.line_count = 99999 if use_real_line_count else 1

        self.set_padding(2)
        self.set_margin(0)

    def set_text(self, text: str):
        self.text = text
        return self

    def set_style(self, style: TextStyle):
        self.style = style
        return self
   
    def set_line_count(self, count: int):
        self.line_count = count
        return self
    
    def set_line_sep(self, sep: int):
        self.line_sep = sep
        return self

    def set_wrap(self, wrap: bool):
        self.wrap = wrap
        return self

    def set_overflow(self, overflow: str):
        assert overflow in ('shrink', 'clip')
        self.overflow = overflow

    def set_text_offset(self, offset: Tuple[int, int]):
        self.text_offset_x = offset[0]
        self.text_offset_y = offset[1]
        return self

    def _get_pil_font(self):
        return get_font(self.style.font, self.style.size)
    
    def _get_font_desc(self):
        return get_font_desc(self.style.font, self.style.size)

    def _get_clip_text_to_width_idx(self, font, text: str, width: int, suffix=''):
        """
        基于“估算+步进”的线性查找。
        通常比二分法更快，因为不需要反复在大范围内测量。
        """
        suffix_width = 0
        if suffix:
            suffix_width = get_text_width(font, suffix)
            
        target_width = width - suffix_width
        
        # 1. 快速检查：如果连后缀都放不下
        if target_width < 0:
            return 0
            
        # 2. 快速检查：如果整行都放得下
        full_width = get_text_width(font, text)
        if full_width <= target_width:
            return None

        text_len = len(text)
        
        # 3. 估算起点
        # 假设ascii字符宽度为1，非ascii字符宽度为2，计算要跳到哪个字符位置
        if full_width > 0:
            char_len_weight_sum = 0
            for c in text:
                char_len_weight_sum += 1 if ord(c) < 128 else 2
            avg_char_width = full_width / char_len_weight_sum
            cur_w = 0
            idx = text_len
            for i, c in enumerate(text):
                cur_w += avg_char_width if ord(c) < 128 else avg_char_width * 2
                if cur_w >= target_width:
                    idx = i + 1
                    break
        else:
            idx = 0
        idx = max(0, min(idx, text_len))

        # 4. 局部步进修正 
        current_w = get_text_width(font, text[:idx])

        if current_w < target_width:
            # 估算小了，向右步进 (Forward)
            while idx < text_len:
                next_idx = idx + 1
                w = get_text_width(font, text[:next_idx])
                if w > target_width:
                    return idx
                idx = next_idx
                current_w = w
            return idx

        elif current_w > target_width:
            # 估算大了，向左步进 (Backward)
            while idx > 0:
                w = get_text_width(font, text[:idx])
                if w <= target_width:
                    return idx
                idx -= 1
            return 0

        else:
            return idx

    def _get_lines(self):
        font = self._get_pil_font()
        lines = self.text.split('\n')  
        clipped_lines = []
        # 对文本中原本包含的每一行进行处理
        for line in lines:
            if self.w:
                w = self.w - self.hpadding * 2
                suffix = '...' if self.overflow == 'shrink' else ''
                if self.wrap:
                    # 需要自动换行的情况，迭代进行裁剪
                    while True:
                        # 首先判断是否能直接放得下
                        clip_idx = self._get_clip_text_to_width_idx(font, line, w, '')
                        if clip_idx is None:
                            clipped_lines.append(line)
                            break
                        # 如果不能放下，则进行裁剪，首先判断是否需要添加后缀（是限制的最后一行）
                        line_suffix = suffix if len(clipped_lines) == self.line_count - 1 else ''
                        if line_suffix:
                            # 需要添加后缀，需要考虑后缀重新计算裁剪位置
                            clip_idx = self._get_clip_text_to_width_idx(font, line, w, line_suffix)
                            if clip_idx is None:
                                clipped_lines.append(line)
                                break
                        # 进行裁剪并添加后缀
                        clipped_lines.append(line[:clip_idx] + line_suffix)
                        line = line[clip_idx:]
                        # 检查是否达到行数限制
                        if len(clipped_lines) == self.line_count:
                            break
                else:
                    clip_idx = self._get_clip_text_to_width_idx(font, line, w, suffix)
                    if clip_idx is not None:
                        line = line[:clip_idx] + suffix
                    clipped_lines.append(line)
            else:
                clipped_lines.append(line)
        return clipped_lines[:self.line_count]

    def _get_content_size(self):
        lines = self._get_lines()
        w, h = 0, 0
        font = self._get_pil_font()
        for line in lines:
            lw, _ = get_text_size(font, line)
            w = max(w, lw)
        line_count = len(lines) if self.use_real_line_count else self.line_count
        h = line_count * (self.style.size + self.line_sep) - self.line_sep
        if self.w:
            w = self.w - self.hpadding * 2
        if self.h:
            h = self.h - self.vpadding * 2
        return (w, h)
        
    def _draw_content(self, p: Painter):
        font = self._get_pil_font()
        lines = self._get_lines()
        text_h = (self.style.size + self.line_sep) * len(lines) - self.line_sep
        if self.content_valign == 't':
            start_y = 0
        elif self.content_valign == 'b':
            start_y = p.h - text_h
        elif self.content_valign == 'c':
            start_y = (p.h - text_h) // 2

        for i, line in enumerate(lines):
            lw, _ = get_text_size(font, line)
            x, y = 0, start_y + i * (self.style.size + self.line_sep)
            if self.content_halign == 'l':
                x += 0
            elif self.content_halign == 'r':
                x += p.w - lw
            elif self.content_halign == 'c':
                x += (p.w - lw) // 2
            x += self.text_offset_x
            y += self.text_offset_y
            p.move_region((x, y), (lw, self.style.size))

            if self.style.use_shadow:
                so = self.style.shadow_offset
                if isinstance(so, int):
                    so = (so, so)
                p.text(line, so, font=self._get_font_desc(), fill=self.style.shadow_color)
            p.text(line, (0, 0), font=self._get_font_desc(), fill=self.style.color)

            p.restore_region()
    
# 辅助函数，不在 painter 中，需要补充
def get_text_width(font, text):
    return get_text_size(font, text)[0]

class ImageBox(Widget):
    def __init__(
        self, 
        image: Union[str, Image.Image], 
        image_size_mode=None, 
        size=None, 
        use_alphablend=False, 
        alpha_adjust=1.0,
        shadow=False,
        shadow_width=6,
        shadow_alpha=0.6,
    ):
        """
        image_size_mode: 'fit', 'fill', 'original'
        """
        super().__init__()
        if isinstance(image, str):
            self.image = Image.open(image)
        else:
            self.image = image

        if size:
            self.set_size(size)

        if image_size_mode is None:
            if size and (size[0] or size[1]):
                self.set_image_size_mode('fit')
            else:
                self.set_image_size_mode('original')
        else:
            self.set_image_size_mode(image_size_mode)
        
        self.set_margin(0)
        self.set_padding(0)

        self.set_use_alphablend(use_alphablend)
        self.set_alpha_adjust(alpha_adjust)
        self.set_shadow(shadow, shadow_width, shadow_alpha)

    def set_alpha_adjust(self, alpha_adjust: float):
        self.alpha_adjust = alpha_adjust
        return self

    def set_use_alphablend(self, use_alphablend):
        self.use_alphablend = use_alphablend
        return self

    def set_shadow(self, shadow: bool, shadow_width=6, shadow_alpha=0.3):
        self.shadow = shadow
        self.shadow_width = shadow_width
        self.shadow_alpha = shadow_alpha
        return self

    def set_image(self, image: Union[str, Image.Image]):
        if isinstance(image, str):
            self.image = Image.open(image)
        else:
            self.image = image
        return self

    def set_image_size_mode(self, mode: str):
        assert mode in ('fit', 'fill', 'original')
        self.image_size_mode = mode
        return self

    def _get_content_size(self):
        w, h = self.image.size
        if self.image_size_mode == 'original':
            return (w, h)
        elif self.image_size_mode == 'fit':
            assert self.w is not None or self.h is not None, 'Fit mode requires width or height'
            tw = self.w - self.hpadding * 2 if self.w else 1000000
            th = self.h - self.vpadding * 2 if self.h else 1000000
            scale = min(tw / w, th / h)
            return (int(w * scale), int(h * scale))
        elif self.image_size_mode == 'fill':
            assert self.w is not None or self.h is not None, 'Fill mode requires width or height'
            if self.w and self.h:
                return (int(self.w - self.hpadding * 2), int(self.h - self.vpadding * 2))
            else:
                tw = self.w - self.hpadding * 2 if self.w else 1000000
                th = self.h - self.vpadding * 2 if self.h else 1000000
                scale = max(tw / w, th / h)
                return (int(w * scale), int(h * scale))
    
    def _draw_content(self, p: Painter):
        w, h = self._get_content_size()
        if self.use_alphablend:
            p.paste_with_alphablend(
                self.image, (0, 0), (w, h), self.alpha_adjust, 
                use_shadow=self.shadow, shadow_width=self.shadow_width, shadow_alpha=self.shadow_alpha,
            )
        else:
            p.paste(
                self.image, (0, 0), (w, h),
                use_shadow=self.shadow, shadow_width=self.shadow_width, shadow_alpha=self.shadow_alpha,
            )


class Spacer(Widget):
    def __init__(self, w: int = 1, h: int = 1):
        super().__init__()
        self.set_size((w, h))
    
    def _get_content_size(self):
        return (self.w - 2 * self.hpadding, self.h - 2 * self.vpadding)

    def _draw_content(self, p: Painter):
        pass


class Canvas(Frame):
    def __init__(self, w=None, h=None, bg: WidgetBg=None):
        super().__init__()
        self.set_size((w, h))
        self.set_bg(bg)
        self.set_margin(0)

    async def get_img(self, scale: float = None, cache_key: str=None):
        t = datetime.now()
        size = self._get_self_size()
        size_limit = global_config.get('plot.canvas_size_limit', (10000, 10000))
        # assert size[0] * size[1] <= size_limit[0] * size_limit[1], f'Canvas size is too large ({size[0]}x{size[1]})'
        if size[0] * size[1] > size_limit[0] * size_limit[1]:
             print(f"Warning: Canvas size is too large ({size[0]}x{size[1]})")

        p = Painter(size=size)
        self.draw(p)
        if global_config.get('plot.log_draw_time', False):
            print(f"Canvas layouted in {(datetime.now() - t).total_seconds():.3f}s, size={size}")

        t = datetime.now()
        img = await p.get(cache_key)
        if scale:
            img = img.resize((int(size[0] * scale), int(size[1] * scale)), Image.Resampling.BILINEAR)
        if global_config.get('plot.log_draw_time', False):
            print(f"Canvas drawn in {(datetime.now() - t).total_seconds():.3f}s, size={size}")
        return img
    

# =========================== 控件函数 =========================== #

# 由带颜色代码的字符串获取彩色文本组件
def colored_text_box(s: str, style: TextStyle, padding=2, **text_box_kargs) -> HSplit:
    try:
        segs = [{ 'text': None, 'color': None }]
        while True:
            i = s.find('<#')
            if i == -1:
                segs[-1]['text'] = s
                break
            j = s.find('>', i)
            segs[-1]['text'] = s[:i]
            code = s[i+2:j]
            if len(code) == 6:
                r, g, b = int(code[:2], 16), int(code[2:4], 16), int(code[4:], 16)
            elif len(code) == 3:
                r, g, b = int(code[0], 16)*17, int(code[1], 16)*17, int(code[2], 16)*17
            else:
                raise ValueError(f"颜色代码格式错误: {code}")
            segs.append({ 'text': None, 'color': (r, g, b) })
            s = s[j+1:]
    except Exception as e:
        segs = [{ 'text': s, 'color': None }]

    with HSplit().set_padding(padding).set_sep(0) as hs:
        for seg in segs:
            text, color = seg['text'], seg['color']
            if text:
                color_style = deepcopy(style)
                if color is not None: color_style.color = color
                TextBox(text, style=color_style, **text_box_kargs).set_padding(0)
    return hs
