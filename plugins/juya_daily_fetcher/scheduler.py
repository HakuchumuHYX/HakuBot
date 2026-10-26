"""轮询橘鸦 RSS：出了新一期就渲染长图、写导语，推给开启早报的群"""

import asyncio
import json

from nonebot import get_driver, logger, require
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from core.access import is_enabled
from plugins.juya_daily_fetcher.config import PLUGIN_ID, STATE_FILE, plugin_config
from plugins.juya_daily_fetcher.parser import FeedItem, parse_feed, parse_issue
from plugins.juya_daily_fetcher.render import render_issue
from plugins.juya_daily_fetcher.summary import summarize
from utils.json_io import atomic_write_json
from utils.logging import get_exc_desc
from utils.network.http import (
    INSECURE_SSL,
    HttpError,
    get_client_session,
    get_effective_proxy,
)
from utils.onebot.forward import ForwardItem, send_forward_msg
from utils.onebot.media import image_segment

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
FORWARD_NAME = "JUYA AI DAILY"
SEND_INTERVAL_SECONDS = 1.5

# 渲染持续失败时只在第一次记 traceback，避免每 15 分钟刷一遍
_render_failing = False


def _load_state() -> dict:
    """etag / last_modified：RSS 条件请求；seen：RSS 里已处理过的 guid"""
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


async def _fetch_feed(state: dict) -> tuple[bytes, dict] | None:
    """RSS 未变（304）返回 None，否则返回（内容，新的缓存头）"""
    headers = {
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        "User-Agent": USER_AGENT,
    }
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    async with get_client_session().get(
        plugin_config.feed_url,
        headers=headers,
        proxy=get_effective_proxy(),
        ssl=False if INSECURE_SSL else None,
    ) as response:
        if response.status == 304:
            return None
        if response.status >= 400:
            raise HttpError(response.status, response.reason or "")
        cache_headers = {
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
        }
        return await response.read(), cache_headers


async def _enabled_groups() -> dict[int, Bot]:
    """开启早报的群 → 负责发送的 Bot；同一个群只由第一个认领它的 Bot 发，避免多开重复"""
    groups: dict[int, Bot] = {}
    for bot in get_driver().bots.values():
        if not isinstance(bot, Bot):
            continue
        try:
            group_list = await bot.get_group_list()
        except Exception as exc:
            logger.warning(
                f"获取机器人 {bot.self_id} 的群列表失败: {get_exc_desc(exc)}"
            )
            continue
        for group in group_list:
            group_id = group["group_id"]
            if group_id not in groups and is_enabled(PLUGIN_ID, str(group_id)):
                groups[group_id] = bot
    return groups


async def _prepare(item: FeedItem) -> tuple[str, str, list[MessageSegment]]:
    """返回（日期，导语，长图消息段）；消息段只生成一次，各群共用"""
    issue = parse_issue(item)
    pages = await render_issue(issue)
    summary = await summarize(issue)
    return issue.date, summary, [image_segment(page) for page in pages]


async def _send(
    bot: Bot, summary: str, images: list[MessageSegment], **target: int
) -> None:
    """target 是 group_id=… 或 user_id=…"""
    if summary:
        api = "send_group_msg" if "group_id" in target else "send_private_msg"
        await bot.call_api(api, message=summary, **target)
        await asyncio.sleep(SEND_INTERVAL_SECONDS)
    # 转发超时视为已发出（不补发），被拒时逐张补发
    await send_forward_msg(
        bot,
        items=[ForwardItem(content=image, name=FORWARD_NAME) for image in images],
        timeout=120.0,
        fallback_interval=SEND_INTERVAL_SECONDS,
        **target,
    )


async def poll() -> None:
    global _render_failing
    groups = await _enabled_groups()
    if not groups:
        logger.debug("没有群开启早报（或 Bot 未连接），跳过本轮")
        return

    state = _load_state()
    try:
        fetched = await _fetch_feed(state)
    except Exception as exc:
        logger.warning(f"拉取早报 RSS 失败，等下一轮: {get_exc_desc(exc)}")
        return
    if fetched is None:
        logger.debug("早报 RSS 未更新（304）")
        return

    xml, cache_headers = fetched
    items = parse_feed(xml)
    seen = state.get("seen")
    new_state = {**cache_headers, "seen": [item.guid for item in items]}
    if seen is None:
        atomic_write_json(STATE_FILE, new_state)
        logger.info(f"已建立基线（{len(items)} 条），本轮不推送历史早报")
        return
    # 只认 guid、不比正文：站点会整批重新生成旧期（调换视频链接顺序、换 BV 号），
    # 按正文变化推送会让群里反复收到旧早报
    new_items = [item for item in items if item.guid not in seen]
    if not new_items:
        atomic_write_json(STATE_FILE, new_state)
        return

    # 停机跨天时会一次出现多期，只推最新一期，旧的一并标为已读
    latest = max(new_items, key=lambda item: item.published)
    try:
        date, summary, images = await _prepare(latest)
    except Exception as exc:
        # 不写 state：下一轮拿不到 304，会重新解析渲染
        if _render_failing:
            logger.debug(f"早报解析/渲染仍失败: {get_exc_desc(exc)}")
        else:
            logger.exception(f"早报解析/渲染失败，之后每轮重试: {get_exc_desc(exc)}")
            _render_failing = True
        return
    if _render_failing:
        logger.info("早报解析/渲染已恢复")
        _render_failing = False

    # 先记已读再发：发送途中重启会漏掉没发完的群，但不会重复推送
    atomic_write_json(STATE_FILE, new_state)
    if len(new_items) > 1:
        logger.info(f"本轮有 {len(new_items)} 期新早报，只推送最新的 {date}")
    sent = 0
    for index, (group_id, bot) in enumerate(groups.items()):
        if index:
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
        try:
            await _send(bot, summary, images, group_id=group_id)
        except Exception as exc:
            logger.warning(f"群 {group_id} 早报发送失败: {get_exc_desc(exc)}")
            continue
        sent += 1
    logger.info(f"早报 {date} 已推送 {sent}/{len(groups)} 个群（{len(images)} 页）")


async def send_debug(bot: Bot) -> str:
    """把 RSS 最新一期发到 debug 私聊，不读写 state"""
    xml, _ = await _fetch_feed({})
    items = parse_feed(xml)
    latest = max(items, key=lambda item: item.published)
    date, summary, images = await _prepare(latest)
    await _send(bot, summary, images, user_id=int(plugin_config.debug_user_id))
    return f"已发送 {date} 的测试早报（{len(images)} 页）"


async def _poll_job() -> None:
    try:
        await poll()
    except Exception as exc:
        logger.exception(f"橘鸦早报轮询失败: {get_exc_desc(exc)}")


scheduler.add_job(
    _poll_job,
    trigger="interval",
    minutes=plugin_config.poll_interval_minutes,
    jitter=30,
    id="juya_daily_fetcher_poll",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
logger.info(f"橘鸦早报轮询任务已注册：间隔 {plugin_config.poll_interval_minutes} 分钟")
