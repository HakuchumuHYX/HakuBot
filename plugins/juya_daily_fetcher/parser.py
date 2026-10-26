"""橘鸦早报 RSS：列出各期，把一期正文拆成目录和新闻条目"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
# 这些标签各自成一个文字块；行内标签（strong、code、a）只取文字
_TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"}
_RELATED_LINKS = re.compile(r"相关链接\s*[:：]?")


@dataclass(frozen=True)
class FeedItem:
    guid: str
    published: datetime
    content_html: str


@dataclass(frozen=True)
class Article:
    number: int
    category: str
    title: str
    # {"type": "paragraph" | "quote", "text"} 或 {"type": "image", "src", "alt"}
    blocks: list[dict]


@dataclass(frozen=True)
class Issue:
    date: str
    pub_date: str
    # [{"category", "items": [{"number", "title"}]}]
    directory: list[dict]
    articles: list[Article]

    @property
    def title(self) -> str:
        return f"橘鸦 AI 早报｜{self.date}"


def parse_feed(xml: bytes) -> list[FeedItem]:
    channel = ET.fromstring(xml).find("channel")
    if channel is None:
        raise ValueError("RSS 缺少 channel")
    items = []
    for node in channel.findall("item"):
        guid = (node.findtext("guid") or node.findtext("link") or "").strip()
        if not guid:
            continue
        pub_date = (node.findtext("pubDate") or "").strip()
        items.append(
            FeedItem(
                guid=guid,
                published=parsedate_to_datetime(pub_date),
                content_html=node.findtext(CONTENT_TAG)
                or node.findtext("description")
                or "",
            )
        )
    if not items:
        raise ValueError("RSS 里没有可用条目")
    return items


class _BlockParser(HTMLParser):
    """把正文切成扁平的块：{"tag", "text"}，图片是 {"tag": "img", "src", "alt"}"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._tag = ""
        self._text: list[str] = []

    def flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        if self._tag and text:
            self.blocks.append({"tag": self._tag, "text": text})
        self._tag = ""
        self._text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _TEXT_TAGS:
            self.flush()
            self._tag = tag
        elif tag == "hr":
            self.flush()
            self.blocks.append({"tag": "hr", "text": ""})
        elif tag == "img":
            self.flush()
            attrs_map = dict(attrs)
            src = (attrs_map.get("src") or "").strip()
            if re.match(r"https?://", src, flags=re.I):
                alt = (attrs_map.get("alt") or "").strip()
                self.blocks.append({"tag": "img", "src": src, "alt": alt})
        elif tag == "br":
            self._text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _TEXT_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if self._tag:
            self._text.append(data)


def _numbered(text: str) -> tuple[int | None, str]:
    """「标题 ↗ #3」→ (3, "标题")"""
    text = text.replace("↗", " ").strip()
    match = re.search(r"(?:^|\s)[#＃]\s*(\d+)$", text)
    if not match:
        return None, text
    return int(match.group(1)), text[: match.start()].strip()


def _article_block(block: dict) -> dict:
    if block["tag"] == "img":
        return {"type": "image", "src": block["src"], "alt": block["alt"]}
    kind = "quote" if block["tag"] == "blockquote" else "paragraph"
    return {"type": kind, "text": block["text"]}


def parse_issue(item: FeedItem) -> Issue:
    """正文结构：「概览」h2 下是 h3 板块 + li 目录；hr 之后按 h2 板块、h3 新闻排列，
    每条新闻以 hr 结尾，末尾的「相关链接」和页脚都不渲染"""
    parser = _BlockParser()
    parser.feed(item.content_html)
    parser.close()
    parser.flush()

    directory: list[dict] = []
    articles: list[Article] = []
    phase = "preamble"
    category = ""
    article: Article | None = None
    for block in parser.blocks:
        tag, text = block["tag"], block.get("text", "")
        if phase == "preamble":
            if tag == "h2" and text == "概览":
                phase = "directory"
        elif phase == "directory":
            if tag == "h3":
                directory.append({"category": text, "items": []})
            elif tag == "li" and directory:
                number, title = _numbered(text)
                items = directory[-1]["items"]
                items.append({"number": number or len(items) + 1, "title": title})
            elif tag == "hr":
                phase = "body"
        elif tag == "h2":
            category = text
            article = None
        elif tag == "h3":
            number, title = _numbered(text)
            article = Article(number or len(articles) + 1, category, title, [])
            articles.append(article)
        elif tag == "hr" or _RELATED_LINKS.fullmatch(text):
            article = None
        elif article is not None:
            article.blocks.append(_article_block(block))

    if not directory or not articles:
        raise ValueError(
            f"早报正文结构无法识别（目录 {len(directory)} 个板块，新闻 {len(articles)} 条）"
        )
    published = item.published.astimezone()
    return Issue(
        date=published.strftime("%Y-%m-%d"),
        pub_date=published.strftime("%Y-%m-%d %H:%M"),
        directory=directory,
        articles=articles,
    )
