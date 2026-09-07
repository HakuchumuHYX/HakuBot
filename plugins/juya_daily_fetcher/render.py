from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from typing import Any

from utils.rendering.engine import render_template_image
from utils.rendering.fonts import font_path
from plugins.juya_daily_fetcher.config import ARTICLE_DIR, TEMPLATE_DIR

ARTICLES_PER_PAGE = 5


def _escape(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def _safe_url(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.match(r"^https?://", text, flags=re.I) else "#"


def _inline_text(text: Any) -> str:
    return _escape(text).replace("\n", "<br>")


def _font_uris() -> dict[str, str]:
    return {
        "font_regular": font_path("Regular").as_uri(),
        "font_bold": font_path("Bold").as_uri(),
        "font_heavy": font_path("Heavy").as_uri(),
    }


def _links_html(block: dict[str, Any]) -> str:
    links = block.get("links") if isinstance(block.get("links"), list) else []
    valid = [
        link
        for link in links
        if isinstance(link, dict)
        and re.match(r"^https?://", str(link.get("url") or ""), flags=re.I)
    ]
    if not valid:
        return ""
    parts = [
        f'<a href="{_escape(_safe_url(link.get("url")))}">{_escape(link.get("text") or link.get("url"))}</a>'
        for link in valid
    ]
    return f'<div class="block-links">{"".join(parts)}</div>'


def _block_html(block: dict[str, Any], index: str) -> str:
    block_type = str(block.get("type") or "paragraph")
    text = str(block.get("text") or "")
    data_attr = f'data-index="{_escape(index)}"'
    if block_type == "heading":
        level = min(6, max(2, int(block.get("level") or 2)))
        return f'<h{level} class="content-block block-{_escape(block_type)}" {data_attr}>{_inline_text(text)}</h{level}>'
    if block_type == "quote":
        return (
            f'<blockquote class="content-block block-{_escape(block_type)}" {data_attr}>'
            f"{_inline_text(text)}</blockquote>{_links_html(block)}"
        )
    if block_type == "code":
        return (
            f'<pre class="article-code content-block block-{_escape(block_type)}" {data_attr}>'
            f"<code>{_escape(text)}</code></pre>"
        )
    if block_type == "list_item":
        cls = "ordered" if block.get("ordered") else "unordered"
        return (
            f'<div class="article-list-item {cls} content-block block-{_escape(block_type)}" {data_attr}>'
            f"{_inline_text(text)}</div>{_links_html(block)}"
        )
    if block_type == "divider":
        return f'<hr class="article-divider content-block block-{_escape(block_type)}" {data_attr}>'
    if block_type == "image":
        src = _safe_url(block.get("src"))
        alt = str(block.get("alt") or "原文图片")
        return (
            f'<figure class="article-image-wrap content-block block-{_escape(block_type)}" {data_attr}>'
            f'<img class="article-image" src="{_escape(src)}" alt="{_escape(alt)}">'
            f'<figcaption class="image-alt">{_escape(alt)}</figcaption></figure>'
        )
    return (
        f'<p class="article-paragraph content-block block-{_escape(block_type)}" {data_attr}>'
        f"{_inline_text(text)}</p>{_links_html(block)}"
    )


def _directory_html(issues: list[dict[str, Any]]) -> str:
    parts = [
        '<div class="directory-page"><div class="directory-kicker">目录 / CONTENTS</div>'
    ]
    for issue_index, issue in enumerate(issues):
        categories = (
            issue.get("categories") if isinstance(issue.get("categories"), list) else []
        )
        if len(issues) > 1:
            heading = (
                issue.get("title") or issue.get("date") or f"第 {issue_index + 1} 期"
            )
            parts.append(f'<div class="directory-issue">{_escape(heading)}</div>')
        for category in categories:
            items = (
                category.get("items") if isinstance(category.get("items"), list) else []
            )
            if not items:
                continue
            item_markup = []
            for item_index, item in enumerate(items):
                number = str(item.get("number") or item_index + 1).zfill(2)
                title = item.get("title") or "未命名条目"
                item_markup.append(
                    '<div class="directory-item">'
                    f'<div class="directory-number">{_escape(number)}</div>'
                    f'<div class="directory-title">{_escape(title)}</div>'
                    "</div>"
                )
            parts.append(
                '<section class="directory-section">'
                '<div class="directory-section-heading">'
                f"<h2>{_escape(category.get('category') or '其他')}</h2>"
                '<div class="section-line"></div></div>'
                f'<div class="directory-list">{"".join(item_markup)}</div>'
                "</section>"
            )
    parts.append("</div>")
    return "".join(parts)


def _article_html(article: dict[str, Any], index: int) -> str:
    blocks = article.get("blocks") if isinstance(article.get("blocks"), list) else []
    number = int(article.get("number") or index + 1)
    block_markup = "\n".join(
        _block_html(block, f"{number}-{block_index}")
        for block_index, block in enumerate(blocks)
        if isinstance(block, dict)
    )
    return (
        '<article class="news-article">'
        '<header class="news-article-header">'
        f'<div class="news-number">{str(number).zfill(2)}</div>'
        f'<div class="news-category">{_escape(article.get("category") or "其他")}</div>'
        f'<div class="news-title">{_escape(article.get("title") or "未命名条目")}</div>'
        "</header>"
        f'<div class="news-body">{block_markup}</div>'
        "</article>"
    )


def _article_page_html(articles: list[dict[str, Any]], offset: int) -> str:
    body = "\n".join(
        _article_html(article, offset + index) for index, article in enumerate(articles)
    )
    return f'<div class="article-page">{body}</div>'


def _header_html(data: dict[str, Any], page: int, total_pages: int) -> str:
    if page > 1:
        return (
            '<section class="continuation-header">'
            '<div class="article-kicker">橘鸦 AI 早报</div>'
            f'<div class="continuation-title">{_escape(data.get("title") or "原文更新")}</div>'
            f'<div class="continuation-meta">CONTINUED · PAGE {page} / {total_pages}</div>'
            "</section>"
        )
    articles = data.get("articles") if isinstance(data.get("articles"), list) else []
    first = articles[0] if articles else {}
    return (
        '<section class="article-header">'
        '<div class="article-kicker">橘鸦 AI 早报</div>'
        f"<h1>{_escape(data.get('title') or first.get('title') or '原文更新')}</h1>"
        f'<div class="article-meta"><span>发布时间：{_escape(first.get("pub_date") or "")}</span></div>'
        '<div class="article-rule"><span></span></div>'
        "</section>"
    )


async def render_document(document: dict[str, Any], event_id: str) -> list[Path]:
    directory = (
        document.get("directory") if isinstance(document.get("directory"), list) else []
    )
    articles = (
        document.get("articles") if isinstance(document.get("articles"), list) else []
    )
    if not directory or not articles:
        raise RuntimeError("早报文档缺少目录或正文")

    total_pages = 1 + ((len(articles) + ARTICLES_PER_PAGE - 1) // ARTICLES_PER_PAGE)
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    font_uris = _font_uris()
    template_dir = str(TEMPLATE_DIR.resolve())
    outputs: list[Path] = []
    for page_index in range(total_pages):
        page_number = page_index + 1
        if page_number == 1:
            content = _directory_html(directory)
        else:
            offset = (page_number - 2) * ARTICLES_PER_PAGE
            page_articles = articles[offset : offset + ARTICLES_PER_PAGE]
            content = _article_page_html(page_articles, offset)
        image_bytes = await render_template_image(
            template_path=template_dir,
            template_name="article-template.html",
            templates={
                "title": _escape(document.get("title") or "原文更新"),
                "date": _escape(document.get("date") or ""),
                "header": _header_html(document, page_number, total_pages),
                "content": content,
                "page": page_number,
                "total_pages": total_pages,
                **font_uris,
            },
            pages={
                "viewport": {"width": 1200, "height": 10},
                "base_url": f"{TEMPLATE_DIR.resolve().as_uri()}/",
            },
            wait=2000,
            device_scale_factor=1,
            screenshot_timeout=60_000,
        )
        suffix = "" if total_pages == 1 else f"-{page_number:02d}"
        output_path = ARTICLE_DIR / f"{event_id}{suffix}.png"
        output_path.write_bytes(image_bytes)
        outputs.append(output_path)
    return outputs
