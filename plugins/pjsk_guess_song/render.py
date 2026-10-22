"""选项图、排行榜、资源图，均由 templates/ 下的模板经 Chromium 渲染"""

from datetime import datetime
from pathlib import Path

from utils.rendering.engine import render_template
from plugins.pjsk_guess_song.config import BACKGROUND_FILE, settings
from plugins.pjsk_guess_song.songs import Song, jacket_url

TEMPLATE_DIR = Path(__file__).parent / "templates"
VERSION = "2.0.0"


async def _render(name: str, footer_lines: list[str], **context) -> bytes:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    footer = [
        *footer_lines,
        f"GuessSong v{VERSION} | Generated on {now}",
        settings.custom_footer_text,
    ]
    return await render_template(
        TEMPLATE_DIR,
        name,
        width=720,
        bg_uri=BACKGROUND_FILE.as_uri() if BACKGROUND_FILE.exists() else "",
        footer_lines=[line for line in footer if line],
        **context,
    )


async def options_image(options: list[Song]) -> bytes:
    """12 个选项的 3×4 曲绘图"""
    items = [{"title": s.title, "jacket": jacket_url(s.jacket)} for s in options]
    return await _render("options.html", [], options=items)


async def leaderboard_image(group_name: str, rows: list[tuple[str, int]]) -> bytes:
    return await _render("leaderboard.html", [], group_name=group_name, rows=rows)


async def resource_image(stats: dict[str, int], data_version: str) -> bytes:
    return await _render(
        "resource.html",
        ["Masterdata acquired from Team-Haruki"],
        total=stats["songs"],
        rows=[("Another Vocal", stats["anvo"]), ("有别名", stats["aliased"])],
        data_version=data_version,
    )
