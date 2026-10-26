"""早报长图：第 1 页是目录，之后每页 5 条新闻"""

from pathlib import Path

from plugins.juya_daily_fetcher.parser import Issue
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"
ARTICLES_PER_PAGE = 5


async def render_issue(issue: Issue) -> list[bytes]:
    chunks = [
        issue.articles[i : i + ARTICLES_PER_PAGE]
        for i in range(0, len(issue.articles), ARTICLES_PER_PAGE)
    ]
    # None 表示目录页
    pages = [None, *chunks]
    return [
        await render_template(
            TEMPLATE_DIR,
            "article-template.html",
            width=1200,
            scale=1,
            timeout=60_000,
            issue=issue,
            articles=articles,
            page=page,
            total_pages=len(pages),
        )
        for page, articles in enumerate(pages, 1)
    ]
