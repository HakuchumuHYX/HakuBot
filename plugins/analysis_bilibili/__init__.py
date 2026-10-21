import asyncio
import re
from typing import List, Union
from aiohttp import ClientError, ClientSession, ClientTimeout
from nonebot import on_regex, logger, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.rule import Rule
from plugins.analysis_bilibili.analysis_bilibili import (
    config,
    b23_extract,
    bili_keyword,
    search_bili_by_title,
    extract_share_url,
)
from core.access import switch_rule
from utils.logging import get_exc_desc
from utils.onebot.media import image_segment


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0",
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
}
HTTP_TIMEOUT = ClientTimeout(total=20, connect=8)

desc_blacklist = config.desc_blacklist
trust_env = config.trust_env
enable_search = config.enable_search
use_on_message = config.use_on_message


async def is_enable_search() -> bool:
    return enable_search


pattern = (
    r"^(?:(?:av|cv)\d+|BV[a-zA-Z0-9]{10})|"
    r"(?:b23\.tv|bili(?:22|23|33|2233)\.cn|\.bilibili\.com|QQ小程序(?:&amp;#93;|&#93;|\])哔哩哔哩).{0,500}"
)

analysis_bili = on_regex(
    pattern,
    rule=switch_rule("analysis_bilibili"),
    priority=10,
    block=False,
)

if use_on_message:
    analysis_bili = on_message(
        rule=switch_rule("analysis_bilibili"), priority=10, block=False
    )


search_bili = on_regex(
    r"^搜视频.*",
    rule=Rule(is_enable_search) & switch_rule("analysis_bilibili"),
    priority=5,
    block=True,
)


def is_image(msg: str) -> bool:
    return msg[-4:].lower() in [
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        "jfif",
        "webp",
    ]


def flatten(container):
    for i in container:
        if isinstance(i, (list, tuple)):
            yield from flatten(i)
        else:
            yield i


def format_msg(msg_list: List[Union[List[str], str]]) -> Message:
    msg = Message()
    for i in flatten(msg_list):
        if not i:
            continue
        elif is_image(i):
            msg.append(image_segment(i))
        else:
            msg.append(MessageSegment.text(i))
    return msg


def _message_json_text(event: Event) -> str:
    """优先用 json 卡片原始 data，避免 str(message) 转义后抓不到 qqdocurl。"""
    for segment in event.get_message():
        if getattr(segment, "type", None) != "json":
            continue
        data = getattr(segment, "data", None) or {}
        raw = data.get("data") if isinstance(data, dict) else data
        if not raw:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        share_url = extract_share_url(text)
        return share_url or text
    return ""


async def send_msg(
    matcher: type[Matcher], msg_list: List[Union[List[str], str, bool]]
) -> None:
    if msg_list is False:
        return
    if not msg_list:
        logger.warning("此次解析的内容为空，接口可能被修改，需要更新！")
        return

    try:
        await matcher.send(format_msg(msg_list))
    except Exception:
        logger.exception(f"发送解析结果失败（可能被风控）: {msg_list}")


async def get_msg(
    event: Event, text: str, search: bool = False
) -> Union[List[str], bool]:
    group_id = str(
        event.group_id
        if hasattr(event, "group_id")
        else event.channel_id
        if hasattr(event, "channel_id")
        else None
    )

    async with ClientSession(
        trust_env=trust_env, headers=headers, timeout=HTTP_TIMEOUT
    ) as session:
        if search:
            text = await search_bili_by_title(text, session=session)
        else:
            share_url = extract_share_url(text)
            if share_url:
                text = share_url
            if re.search(r"(b23.tv)|(bili(22|23|33|2233).cn)", text, re.I):
                # 提前处理短链接，避免解析到其他的
                try:
                    text = await b23_extract(text, session=session)
                except (ClientError, asyncio.TimeoutError) as e:
                    # 被动解析，外部超时不回复
                    logger.warning(f"解析 b23 短链失败: {get_exc_desc(e)}")
                    return False

        msg = await bili_keyword(group_id, text, session=session)

    if msg:
        if isinstance(msg, str):
            # 说明是错误信息
            await analysis_bili.finish(msg)

        if group_id in desc_blacklist:
            if msg[-1].startswith("简介"):
                msg[-1] = ""

    return msg


@analysis_bili.handle()
async def handle_analysis(event: Event) -> None:
    message = event.get_message()
    # on_message
    if use_on_message:
        # 不解析转发消息，可能会过长导致匹配时间过长
        for segment in message:
            if segment.type == "forward":
                logger.debug("analysis_bilibili 忽略转发消息")
                return
        if not re.search(pattern, str(message)):
            return
    # on_regex
    text = _message_json_text(event) or str(message)
    msg = await get_msg(event, text)
    await send_msg(analysis_bili, msg)


@search_bili.handle()
async def handle_search(event: Event) -> None:
    msg = await get_msg(event, str(event.get_message())[3:].strip(), search=True)
    await send_msg(search_bili, msg)
