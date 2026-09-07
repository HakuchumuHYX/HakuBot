from nonebot.log import logger
from utils.rendering.help import render_help, help_to_text
from utils.rendering.models import RenderError
from .media import image_segment


async def send_help(matcher, document, *, theme="auto", force=False):
    try:
        rendered = await render_help(document, theme=theme, force=force)
    except RenderError:
        logger.exception("Help image unavailable; sending the same document as text")
        text = help_to_text(document)
        for start in range(0, len(text), 2000):
            await matcher.send(text[start : start + 2000])
        return
    for page in rendered.pages:
        await matcher.send(image_segment(page.data))
