"""bili_dyn_sub 专栏全文：opus 详情的段落 → 页面块（templates/article.html 渲染）。

不经过 Markdown：详情给的本来就是结构化段落（正文、图片、引用、列表、链接卡片，
文字带字号、加粗、颜色），直接映射成 HTML 才能保住原排版。md 还会把 `#话题#`、`#1`
当成一级标题，把歌词的单换行并成一段。文字一律交给 Jinja 自动转义。

段落类型按哪个字段非空来分，不依赖 para_type 编号；只有引用（para_type 4）
没有专门字段，内容放在 text 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from nonebot import logger

from plugins.bili_dyn_sub.parser import _as_int, _as_str, _dget, _normalize_url

BASE_FONT_SIZE = 17  # B 站专栏正文字号，模板同样以 17px 为基准
HEADING_FONT_SIZE = (
    20  # 字号到这里（B 站的 large 档）的整段视为小标题，分页时不让它落在页底
)
EXCERPT_CHARS = 100
EXCERPT_LINES = 4
_QUOTE_PARA_TYPE = 4

# 颜色、背景原样交给 CSS：B 站写的是 `var(--Bl5, #6188ff)`，未定义变量时浏览器自动用后面的兜底色。
# 只放行这些字符，外部输入不能借 style 写出别的声明。
_SAFE_CSS_RE = re.compile(r"^[#\w\s(),.%-]+$")
# `rgba(var(--Ga9_rgb), 0.6)` 没有兜底色，变量又未定义，整条会失效；按正文色加同样的透明度处理
_RGB_VAR_RE = re.compile(r"^rgba\(var\(--\w+_rgb\),\s*([\d.]+)\)$")
_INK_RGB = "24, 25, 28"
# 原文里夹着 U+0005 这类控制字符，字体画成方框，还会让整段因字号不一被认不出是小标题
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class Article:
    blocks: list[dict]
    words: int  # 不含空白的字数
    excerpt: str


def parse_article(item: Any) -> Article | None:
    """详情 data.item → Article；没有正文段落（付费专栏、结构变了）返回 None"""
    paragraphs: list = []
    for module in _dget(item, "modules", []):
        if _dget(module, "module_type") == "MODULE_TYPE_CONTENT":
            paragraphs = _dget(_dget(module, "module_content", {}), "paragraphs", [])
    if not isinstance(paragraphs, list) or not paragraphs:
        return None

    blocks: list[dict] = []
    unknown: set[str] = set()
    for para in paragraphs:
        block = _block(para)
        if block is None:
            unknown.add(_as_str(_dget(para, "para_type", "?")))
        else:
            blocks.append(block)
    if unknown:
        logger.warning(
            f"专栏 {_dget(item, 'id_str', '?')} 有无法识别的段落类型，已略过: {sorted(unknown)}"
        )

    texts = [b["plain"] for b in blocks if b.get("plain", "").strip()]
    return Article(
        blocks=blocks,
        words=sum(len(re.sub(r"\s", "", t)) for t in texts),
        excerpt=excerpt(texts),
    )


def excerpt(texts: list[str]) -> str:
    """卡片里的节选：前几行、前一百来字"""
    lines = [line for t in texts for line in t.splitlines() if line.strip()]
    text = "\n".join(lines[:EXCERPT_LINES])
    if len(text) > EXCERPT_CHARS:
        return text[:EXCERPT_CHARS] + "…"
    return text + ("…" if len(lines) > EXCERPT_LINES else "")


# ---------------------------------------------------------------- 段落


def _block(para: Any) -> dict | None:
    align = {1: "center", 2: "right"}.get(
        _as_int(_dget(_dget(para, "format", {}), "align", 0)), ""
    )

    if heading := _dget(para, "heading"):
        spans = _spans(_dget(heading, "nodes", []))
        return _text_block("heading", spans, align, heading=True)

    if text := _dget(para, "text"):
        spans = _spans(_dget(text, "nodes", []))
        kind = "quote" if _dget(para, "para_type") == _QUOTE_PARA_TYPE else "p"
        return _text_block(kind, spans, align, heading=_is_heading(spans))

    if pic := _dget(para, "pic"):
        pics = [
            {
                "url": _normalize_url(_dget(p, "url")),
                "width": _as_int(_dget(p, "width", 0)),
                "height": _as_int(_dget(p, "height", 0)),
            }
            for p in _dget(pic, "pics", [])
        ]
        pics = [p for p in pics if p["url"] and p["width"] > 0 and p["height"] > 0]
        if not pics:
            return None
        # 极扁的图（资讯站用 592×9 的图当分隔线）分页时当分隔线：在它后面断页最自然
        sep = all(p["height"] * 20 <= p["width"] for p in pics)
        return {"kind": "pic", "pics": pics, "align": align, "sep": sep}

    if line := _dget(para, "line"):
        url = _normalize_url(_dget(_dget(line, "pic", {}), "url"))
        return {"kind": "line", "url": url, "sep": True}

    if lst := _dget(para, "list"):
        items = [
            {
                "level": max(_as_int(_dget(i, "level", 1)), 1),
                "spans": _spans(_dget(i, "nodes", [])),
            }
            for i in _dget(lst, "items", [])
        ]
        return {
            "kind": "list",
            "ordered": _as_int(_dget(lst, "style", 0)) == 1,
            "entries": items,
            "plain": "\n".join(_plain(i["spans"]) for i in items),
        }

    if code := _dget(para, "code"):
        content = _as_str(_dget(code, "content", ""))
        return {"kind": "code", "text": content, "plain": content}

    if card := _dget(para, "link_card"):
        return _card_block(_dget(card, "card", {}))

    return None


def _text_block(kind: str, spans: list[dict], align: str, *, heading: bool) -> dict:
    plain = _plain(spans)
    return {
        "kind": kind,
        "spans": spans,
        "align": align,
        "plain": plain,
        "blank": not plain.strip() and not any(s["emoji"] for s in spans),
        "heading": heading,
    }


def _is_heading(spans: list[dict]) -> bool:
    sized = [s for s in spans if s["text"].strip()]
    return bool(sized) and all(s["size"] >= HEADING_FONT_SIZE for s in sized)


def _plain(spans: list[dict]) -> str:
    return "".join(s["text"] for s in spans).rstrip("\n")


def _card_block(card: Any) -> dict:
    """链接卡片：视频画成带封面的横条，其余只给标题（eva3_* 的标题在 info 里）；
    拿不到内容的（ITEM_NULL）只给 "视频" 这类类型名"""
    if video := _dget(_dget(card, "eva3_video", {}), "info", {}):
        return {
            "kind": "video",
            "cover": _normalize_url(_dget(video, "cover")),
            "title": _as_str(_dget(video, "title", "")),
            "duration": _as_str(
                _dget(_dget(video, "video_ts", {}), "duration_text", "")
            ),
            "stats": [
                _as_str(_dget(s, "text", "")) for s in _dget(video, "stat_infos", [])
            ],
        }
    for value in card.values() if isinstance(card, dict) else ():
        title = _as_str(
            _dget(value, "title") or _dget(_dget(value, "info", {}), "title", "")
        ).strip()
        if title:
            return {"kind": "card", "text": title}
    text = _as_str(_dget(_dget(card, "item_null", {}), "text", "")).strip()
    return {"kind": "card", "text": text or "链接"}


# ---------------------------------------------------------------- 文字片段


def _spans(nodes: Any) -> list[dict]:
    spans: list[dict] = []
    for node in nodes if isinstance(nodes, list) else ():
        node_type = _as_str(_dget(node, "type", ""))
        if node_type == "TEXT_NODE_TYPE_WORD":
            word = _dget(node, "word", {})
            spans.append(_word_span(word))
        elif node_type == "TEXT_NODE_TYPE_RICH":
            rich = _dget(node, "rich", {})
            icon = _as_str(_dget(_dget(rich, "emoji", {}), "icon_url", ""))
            if icon:
                spans.append(_span("", emoji=_normalize_url(icon)))
            else:
                # 网址、@、话题、视频号：都只显示 B 站给的文字，染成链接色
                spans.append(_span(_as_str(_dget(rich, "text", "")), link=True))
        elif node_type == "TEXT_NODE_TYPE_FORMULA":
            latex = _dget(_dget(node, "formula", {}), "latex_content", "")
            spans.append(_span(_as_str(latex)))
    return spans


def _word_span(word: Any) -> dict:
    style = _dget(word, "style", {})
    size = _as_int(_dget(word, "font_size", BASE_FONT_SIZE), BASE_FONT_SIZE)
    css: list[str] = []
    if size != BASE_FONT_SIZE:
        css.append(f"font-size:{size}px")
    if _dget(style, "bold") is True:
        css.append("font-weight:600")
    if _dget(style, "italic") is True:
        css.append("font-style:italic")
    decorations = [
        name
        for key, name in (("strikethrough", "line-through"), ("underline", "underline"))
        if _dget(style, key) is True
    ]
    if decorations:
        css.append(f"text-decoration:{' '.join(decorations)}")
    if color := _css_color(_as_str(_dget(word, "color", ""))):
        css.append(f"color:{color}")
    background = _as_str(_dget(style, "background", "")).strip()
    if background and _SAFE_CSS_RE.match(background):
        css.append(f"background:{background}")
    return _span(_as_str(_dget(word, "words", "")), style=";".join(css), size=size)


def _css_color(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if match := _RGB_VAR_RE.match(raw):
        return f"rgba({_INK_RGB}, {match.group(1)})"
    return raw if _SAFE_CSS_RE.match(raw) else ""


def _span(
    text: str,
    *,
    style: str = "",
    size: int = BASE_FONT_SIZE,
    link: bool = False,
    emoji: str = "",
) -> dict:
    return {
        "text": _CONTROL_RE.sub("", text),
        "style": style,
        "size": size,
        "link": link,
        "emoji": emoji,
    }
