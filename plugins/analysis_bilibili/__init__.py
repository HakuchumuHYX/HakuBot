"""B 站链接解析：群里出现视频、番剧、直播、专栏、动态的链接或分享卡片时，回一段文字简介

移植自 nonebot-plugin-analysis-bilibili（MIT，见同目录 LICENSE）
"""

import re

from nonebot import logger, on_regex
from nonebot.adapters.onebot.v11 import MessageEvent

from core.access import switch_rule
from plugins.analysis_bilibili.parser import DOMAINS, parse
from utils.logging import get_exc_desc

_LINK = re.compile(rf"(?:https?://)?[\w.-]*{DOMAINS}[^\s\"'<>]*", re.I)


def _message_text(event: MessageEvent) -> str:
    """分享卡片取 json 段原文（链接在 qqdocurl / jumpUrl 里），否则取纯文本"""
    for segment in event.get_message():
        if segment.type == "json":
            return str(segment.data["data"]).replace("\\/", "/")
    return event.get_plaintext()


analysis_bili = on_regex(
    rf"^(?:av\d+|cv\d+|BV[0-9A-Za-z]{{10}})|{DOMAINS}",
    rule=switch_rule("analysis_bilibili"),
    priority=10,
    block=False,
)


@analysis_bili.handle()
async def handle_analysis(event: MessageEvent) -> None:
    text = _message_text(event)
    link = m[0] if (m := _LINK.search(text)) else text
    try:
        reply = await parse(link)
    except Exception as exc:
        # 被动解析，失败不往群里发
        logger.warning(f"B 站链接解析失败（{link[:100]}）: {get_exc_desc(exc)}")
        return
    if reply is None:
        logger.debug(f"没有可解析的 B 站链接: {link[:100]}")
        return
    await analysis_bili.send(reply.text)
