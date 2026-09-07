from __future__ import annotations

import hashlib
import html as html_lib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


class FeedTextParser(HTMLParser):
    """Turn RSS HTML into fallback readable plain text."""

    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "dd",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            if alt.strip():
                self.parts.append(f"[图片：{alt.strip()}]")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if not self._skip_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = FeedTextParser()
    parser.feed(value or "")
    parser.close()
    text = html_lib.unescape("".join(parser.parts))
    lines: list[str] = []
    blank = False
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
        if line:
            lines.append(line)
            blank = False
        elif not blank and lines:
            lines.append("")
            blank = True
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


class RawArticleParser(HTMLParser):
    """Convert article HTML into safe, semantic, renderer-friendly blocks."""

    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "form"}
    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    _BLOCK_TAGS = _HEADING_TAGS | {
        "p",
        "blockquote",
        "pre",
        "li",
        "div",
        "section",
        "article",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.list_stack: list[bool] = []
        self.active_link: dict[str, str] | None = None
        self.skip_depth = 0

    def _start_block(self, block_type: str, **extra: Any) -> None:
        self._flush()
        self.current = {"type": block_type, "raw": "", "links": [], **extra}

    def _append_data(self, data: str) -> None:
        if self.skip_depth or not data:
            return
        if self.current is None:
            self._start_block("paragraph")
        assert self.current is not None
        self.current["raw"] += data
        if self.active_link is not None:
            self.active_link["text"] += data

    def _flush(self) -> None:
        if self.current is None:
            return
        block = self.current
        self.current = None
        raw = str(block.pop("raw", ""))
        block_type = str(block.get("type", "paragraph"))
        if block_type == "code":
            text = raw.strip("\n")
        else:
            text = re.sub(r"\s+", " ", raw).strip()
        block["text"] = text
        links = []
        for link in block.get("links", []):
            href = str(link.get("url", "")).strip()
            label = re.sub(r"\s+", " ", str(link.get("text", ""))).strip()
            if re.match(r"^https?://", href, flags=re.I):
                links.append({"text": label or "查看来源", "url": href})
        block["links"] = links
        if block_type == "list_item":
            if not text:
                return
        elif not text and block_type not in {"divider"}:
            return
        self.blocks.append(block)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        if tag in self._SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self._HEADING_TAGS:
            self._start_block("heading", level=int(tag[1]))
        elif tag == "p":
            self._start_block("paragraph")
        elif tag == "blockquote":
            self._start_block("quote")
        elif tag == "pre":
            self._start_block("code")
        elif tag in {"ul", "ol"}:
            self._flush()
            self.list_stack.append(tag == "ol")
        elif tag == "li":
            self._start_block(
                "list_item",
                ordered=self.list_stack[-1] if self.list_stack else False,
            )
        elif tag == "hr":
            self._flush()
            self.blocks.append({"type": "divider"})
        elif tag == "br":
            self._append_data("\n")
        elif tag == "a":
            self.active_link = {"text": "", "url": attrs_map.get("href", "")}
            if self.current is None:
                self._start_block("paragraph")
        elif tag == "img":
            self._flush()
            src = attrs_map.get("src", "").strip()
            alt = attrs_map.get("alt", "").strip()
            if re.match(r"^https?://", src, flags=re.I):
                self.blocks.append({"type": "image", "src": src, "alt": alt})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "a":
            if self.current is not None and self.active_link is not None:
                self.current.setdefault("links", []).append(self.active_link)
            self.active_link = None
        elif tag in self._BLOCK_TAGS or tag in {"ul", "ol"}:
            self._flush()
            if tag in {"ul", "ol"} and self.list_stack:
                self.list_stack.pop()

    def handle_data(self, data: str) -> None:
        self._append_data(data)

    def finish(self) -> list[dict[str, Any]]:
        self._flush()
        return self.blocks


def parse_article_blocks(
    content_html: str, fallback_text: str = ""
) -> list[dict[str, Any]]:
    parser = RawArticleParser()
    parser.feed(content_html or "")
    parser.close()
    blocks = parser.finish()
    if not blocks and fallback_text.strip():
        return [{"type": "paragraph", "text": fallback_text.strip(), "links": []}]
    return blocks


def _numbered_text(value: str) -> tuple[int | None, str]:
    text = re.sub(r"\s+", " ", value or "").replace("↗", " ").strip()
    match = re.search(r"(?:^|\s)[#＃]\s*(\d+)\s*$", text)
    if not match:
        return None, text
    return int(match.group(1)), text[: match.start()].strip()


def _is_related_links_label(value: str) -> bool:
    return bool(re.fullmatch(r"相关链接\s*[:：]?", re.sub(r"\s+", "", value or "")))


def _is_footer_note(value: str) -> bool:
    text = re.sub(r"\s+", "", value or "")
    return text.startswith("提示") and ("AI" in text or "辅助创作" in text)


def _is_footer_source_links(value: str) -> bool:
    text = re.sub(r"\s+", "", value or "")
    return "查看网页全文" in text or "查看Markdown" in text


def _clean_render_block(block: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: value for key, value in block.items() if key != "links"}
    cleaned["links"] = []
    return cleaned


def parse_clean_issue(
    content_html: str,
    fallback_text: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract the issue directory and complete, non-splittable news items."""
    blocks = parse_article_blocks(content_html, fallback_text)
    directory: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    current_directory: dict[str, Any] | None = None
    current_category = ""
    current_article: dict[str, Any] | None = None
    related_links = False
    phase = "preamble"

    def finish_article() -> None:
        nonlocal current_article
        if current_article is not None and current_article.get("title"):
            articles.append(current_article)
        current_article = None

    for block in blocks:
        block_type = str(block.get("type", "paragraph"))
        text = str(block.get("text", "") or "").strip()
        level = int(block.get("level", 0) or 0)

        if phase == "preamble":
            if block_type == "heading" and level == 2 and text == "概览":
                phase = "directory"
            continue

        if phase == "directory":
            if block_type == "heading" and level == 3:
                current_directory = {"category": text, "items": []}
                directory.append(current_directory)
            elif block_type == "list_item" and current_directory is not None and text:
                number, title = _numbered_text(text)
                if title:
                    items = current_directory["items"]
                    items.append(
                        {
                            "number": number if number is not None else len(items) + 1,
                            "title": title,
                        }
                    )
            elif block_type == "divider":
                phase = "body"
            elif block_type == "heading" and level == 2:
                phase = "body"
                current_category = text
            continue

        if block_type == "heading" and level == 2:
            finish_article()
            current_category = text
            related_links = False
            continue

        if block_type == "heading" and level == 3:
            number, title = _numbered_text(text)
            if current_article is not None and number is None:
                current_article["blocks"].append(_clean_render_block(block))
                continue
            finish_article()
            current_article = {
                "number": number if number is not None else len(articles) + 1,
                "category": current_category,
                "title": title or text,
                "blocks": [],
            }
            related_links = False
            continue

        if current_article is None:
            continue
        if block_type == "divider":
            related_links = False
            continue
        if _is_related_links_label(text):
            related_links = True
            continue
        if related_links or _is_footer_note(text) or _is_footer_source_links(text):
            continue
        if block_type == "paragraph" and not text:
            continue
        current_article["blocks"].append(_clean_render_block(block))

    finish_article()
    if not directory or not articles:
        raise ValueError(
            f"RSS article structure is not recognized (directory={len(directory)}, articles={len(articles)})"
        )
    return directory, articles


def parse_date(value: str) -> datetime:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        return datetime.min.replace(tzinfo=timezone.utc)


def parse_feed(xml: bytes) -> list[dict[str, Any]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS channel is missing")
    items: list[dict[str, Any]] = []
    for node in channel.findall("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        guid = (node.findtext("guid") or link or title).strip()
        pub_date = (node.findtext("pubDate") or "").strip()
        description_html = (node.findtext("description") or "").strip()
        content_html = (
            node.findtext(f"{{{CONTENT_NS}}}encoded") or description_html
        ).strip()
        if not guid:
            continue
        fingerprint_source = "\n".join(
            [guid, title, link, pub_date, content_html]
        ).encode("utf-8", "replace")
        items.append(
            {
                "guid": guid,
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "description": html_to_text(description_html),
                "content_html": content_html,
                "content_text": html_to_text(content_html),
                "fingerprint": hashlib.sha256(fingerprint_source).hexdigest(),
            }
        )
    if not items:
        raise ValueError("RSS contains no usable items")
    return items


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_seen(items: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        item["guid"]: {
            "fingerprint": item["fingerprint"],
            "title": item["title"],
            "link": item["link"],
            "pub_date": item["pub_date"],
        }
        for item in items[:30]
    }


def event_id_for(updates: list[dict[str, Any]]) -> str:
    basis = "|".join(f"{item['guid']}:{item['fingerprint']}" for item in updates)
    return f"rss-juya-{hashlib.sha256(basis.encode()).hexdigest()[:32]}"


def date_label(item: dict[str, Any]) -> str:
    dt = parse_date(str(item.get("pub_date", "")))
    if dt == datetime.min.replace(tzinfo=timezone.utc):
        return str(item.get("title", ""))
    return dt.astimezone().strftime("%Y-%m-%d")


def build_raw_document(feed_url: str, updates: list[dict[str, Any]]) -> dict[str, Any]:
    if not updates:
        raise ValueError("cannot build article document without updates")
    directory: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []
    raw_articles: list[dict[str, Any]] = []
    for item in updates:
        content_html = str(item.get("content_html") or "")
        content_text = str(item.get("content_text") or item.get("description") or "")
        issue_date = date_label(item)
        issue_title = str(item.get("title", ""))
        issue_link = str(item.get("link", ""))
        issue_directory, issue_articles = parse_clean_issue(content_html, content_text)
        directory.append(
            {
                "title": issue_title,
                "date": issue_date,
                "categories": issue_directory,
            }
        )
        for article in issue_articles:
            article.update(
                {
                    "issue_title": issue_title,
                    "issue_date": issue_date,
                    "issue_url": issue_link,
                    "pub_date": str(item.get("pub_date", "")),
                }
            )
            articles.append(article)
        raw_articles.append(
            {
                "title": issue_title,
                "link": issue_link,
                "pub_date": str(item.get("pub_date", "")),
                "date": issue_date,
                "raw_html": content_html,
            }
        )
    first_date = directory[0]["date"]
    title = f"橘鸦 AI 早报｜{first_date}"
    if len(directory) > 1:
        title = f"橘鸦 AI 早报｜{first_date} 等 {len(directory)} 期原文更新"
    return {
        "mode": "clean_article",
        "title": title,
        "date": first_date,
        "source_feed": feed_url,
        "source_url": raw_articles[0]["link"],
        "directory": directory,
        "articles": articles,
        "raw_articles": raw_articles,
        "generated_at": now_iso(),
    }
