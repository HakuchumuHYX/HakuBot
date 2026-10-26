"""HLTV 群命令与回复。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytz

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import (
    GROUP,
    GROUP_ADMIN,
    GROUP_OWNER,
    GroupMessageEvent,
    Message,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary
from plugins.hltv_sub.client import HLTVFetchError, hltv_client
from plugins.hltv_sub.config import plugin_config
from plugins.hltv_sub.data_manager import EventSubscription, data_manager
from plugins.hltv_sub.handler import hltv_handler
from plugins.hltv_sub.render import (
    render_events,
    render_matches,
    render_results,
    render_stats,
)
from plugins.hltv_sub.scheduler import hltv_scheduler

NO_ACTIVE_HINT = "当前没有进行中的订阅赛事\n使用 我的订阅 查看已订阅赛事的日期"
# 列表命令接受周期检查留下的 10 分钟内页面，避免每次查询都排队现抓。
LIST_MAX_AGE_SECONDS = 600


async def _hltv_enabled(event: GroupMessageEvent) -> bool:
    return data_manager.is_enabled(event.group_id)


# 没开 HLTV 的群不响应；开关命令本身不挂这条
ENABLED = Rule(command_boundary, _hltv_enabled)


async def _fetch(matcher: type[Matcher], what: str, coro):
    """抓取失败时直接结束命令：暂停访问回原因，其他错误记日志回一句。"""
    try:
        return await coro
    except HLTVFetchError as e:
        await matcher.finish(str(e))
    except Exception as e:
        logger.exception(f"获取{what}失败: {e}")
        await matcher.finish(f"获取{what}失败，HLTV 可能暂时无法访问")


def _by_event_id(subscriptions: list[EventSubscription]) -> list[EventSubscription]:
    return sorted(
        subscriptions,
        key=lambda x: int(x.event_id) if x.event_id.isdigit() else x.event_id,
    )


def _may_have_matches(sub: EventSubscription) -> bool:
    """赛事结束后订阅还会保留几天等赛果推送，但赛程页已不再有比赛。
    欧洲赛事决赛常打到北京时间次日凌晨，结束日之后再留 12 小时。"""
    now = datetime.now(pytz.timezone(plugin_config.timezone))
    return date.fromisoformat(sub.end_date) >= (now - timedelta(hours=12)).date()


# event列表命令
event_list = on_command(
    "event列表",
    aliases={"赛事列表", "events"},
    rule=ENABLED,
    permission=GROUP,
    priority=5,
    block=True,
)


@event_list.handle()
async def handle_event_list():
    await event_list.send("正在获取赛事列表，请稍候...")
    events = await _fetch(event_list, "赛事列表", hltv_client.get_big_events())
    ongoing = [e for e in events if e.is_ongoing]
    upcoming = [e for e in events if not e.is_ongoing]
    await event_list.finish(image_segment(await render_events(ongoing, upcoming)))


# event订阅命令
event_subscribe = on_command(
    "event订阅",
    aliases={"订阅赛事", "subscribe"},
    rule=ENABLED,
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    priority=5,
    block=True,
)


@event_subscribe.handle()
async def handle_event_subscribe(args: Message = CommandArg()):
    event_id = args.extract_plain_text().strip()
    if not event_id:
        await event_subscribe.finish("请提供赛事ID，例如：event订阅 7148")
    if data_manager.is_subscribed(event_id):
        await event_subscribe.finish(f"已经订阅了赛事 #{event_id}")

    await event_subscribe.send("正在获取赛事信息...")
    try:
        events = await hltv_client.get_big_events()
        event_info = next((e for e in events if e.id == event_id), None)
        if event_info is None:
            event_info = await hltv_client.get_event_info(event_id)
        # 在写入订阅前完成网络操作，避免失败后留下半完成订阅。
        if event_info.is_ongoing:
            await hltv_handler.initialize_event_results_as_notified(event_id)
    except HLTVFetchError as e:
        await event_subscribe.finish(f"订阅未完成：{e}")
    except ValueError as e:
        logger.warning(f"赛事 {event_id} 信息解析失败: {e}")
        await event_subscribe.finish(f"无法获取赛事 #{event_id} 的信息，未创建订阅")
    except Exception as e:
        logger.exception(f"订阅赛事失败: {e}")
        await event_subscribe.finish("订阅失败，HLTV 可能暂时无法访问")

    try:
        created = data_manager.subscribe_event(EventSubscription(
            event_id=event_id,
            event_title=event_info.title,
            start_date=event_info.start_date,
            end_date=event_info.end_date,
        ))
    except OSError:
        logger.exception("订阅时保存数据失败")
        await event_subscribe.finish("订阅未完成：保存数据失败，请查看日志")
    if not created:
        await event_subscribe.finish(f"已经订阅了赛事 #{event_id}")

    hltv_scheduler.sync_events()
    await event_subscribe.finish(f"✅ 成功订阅赛事：{event_info.title}")


# event取消订阅命令
event_unsubscribe = on_command(
    "event取消订阅",
    aliases={"取消订阅赛事", "unsubscribe"},
    rule=ENABLED,
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    priority=5,
    block=True,
)


@event_unsubscribe.handle()
async def handle_event_unsubscribe(args: Message = CommandArg()):
    event_id = args.extract_plain_text().strip()
    if not event_id:
        await event_unsubscribe.finish("请提供赛事ID，例如：event取消订阅 7148")

    try:
        removed = data_manager.unsubscribe_event(event_id)
    except OSError:
        logger.exception("取消订阅时保存数据失败")
        await event_unsubscribe.finish("取消订阅未完成：保存数据失败")
    if not removed:
        await event_unsubscribe.finish(f"未订阅赛事 #{event_id}")
    hltv_scheduler.sync_events()
    await event_unsubscribe.finish(f"✅ 已取消订阅赛事 #{event_id}")


# 我的订阅命令
my_subscriptions = on_command(
    "我的订阅",
    aliases={"订阅列表", "mysub"},
    rule=ENABLED,
    permission=GROUP,
    priority=5,
    block=True,
)


@my_subscriptions.handle()
async def handle_my_subscriptions():
    subscriptions = data_manager.get_subscribed_events()
    if not subscriptions:
        await my_subscriptions.finish(
            "当前没有订阅任何赛事\n使用 event列表 查看可订阅的赛事"
        )

    msg = "📋 已订阅的赛事：\n"
    for sub in subscriptions:
        msg += f"• #{sub.event_id} {sub.event_title}\n"
        msg += f"  📅 {sub.start_date[5:]} ~ {sub.end_date[5:]}\n"
    await my_subscriptions.finish(msg.strip())


# 比赛列表
matches_list = on_command(
    "matches列表",
    aliases={"比赛列表", "matches"},
    rule=ENABLED,
    permission=GROUP,
    priority=5,
    block=True,
)


@matches_list.handle()
async def handle_matches_list():
    subscriptions = [
        sub for sub in hltv_handler.active_subscriptions() if _may_have_matches(sub)
    ]
    if not subscriptions:
        await matches_list.finish(NO_ACTIVE_HINT)

    await matches_list.send("正在获取比赛列表，请稍候...")

    async def collect():
        pages = {}
        for sub in _by_event_id(subscriptions):
            page = await hltv_client.get_event_matches(
                sub.event_id, include_partial_tbd=True, max_age=LIST_MAX_AGE_SECONDS,
            )
            if page.matches:
                pages[f"#{sub.event_id} {sub.event_title}"] = page.matches
        return pages

    matches_by_event = await _fetch(matches_list, "比赛列表", collect())
    if not matches_by_event:
        await matches_list.finish("暂无比赛")
    all_matches = [m for matches in matches_by_event.values() for m in matches]
    live_count = sum(m.is_live for m in all_matches)
    img = await render_matches(matches_by_event, live_count, len(all_matches) - live_count)
    await matches_list.finish(image_segment(img))


# 比赛结果
results_list = on_command(
    "results列表",
    aliases={"结果列表", "results"},
    rule=ENABLED,
    permission=GROUP,
    priority=5,
    block=True,
)


@results_list.handle()
async def handle_results_list():
    subscriptions = hltv_handler.active_subscriptions()
    if not subscriptions:
        await results_list.finish(NO_ACTIVE_HINT)

    await results_list.send("正在获取比赛结果，请稍候...")

    async def collect():
        pages = {}
        for sub in _by_event_id(subscriptions):
            results = await hltv_client.get_event_results(
                sub.event_id, max_age=LIST_MAX_AGE_SECONDS,
            )
            if results:
                pages[f"#{sub.event_id} {sub.event_title}"] = results
        return pages

    results_by_event = await _fetch(results_list, "比赛结果", collect())
    if not results_by_event:
        await results_list.finish("暂无比赛结果")
    await results_list.finish(image_segment(await render_results(results_by_event)))


# 比赛数据
stats_cmd = on_command("stats", rule=ENABLED, permission=GROUP, priority=5, block=True)


@stats_cmd.handle()
async def handle_stats(args: Message = CommandArg()):
    match_id = args.extract_plain_text().strip()
    if match_id:
        await stats_cmd.send(f"正在获取比赛 #{match_id} 的数据...")
        stats = await _fetch(stats_cmd, "比赛数据", hltv_client.get_match_stats(match_id))
    else:
        subscriptions = hltv_handler.active_subscriptions()
        if not subscriptions:
            await stats_cmd.finish(f"{NO_ACTIVE_HINT}\n或提供比赛ID，例如：stats 2370931")
        await stats_cmd.send("正在获取最新比赛数据...")

        async def latest():
            for sub in subscriptions:
                if stats := await hltv_client.get_latest_result_with_stats(
                    sub.event_id, sub.event_title
                ):
                    return stats
            return None

        stats = await _fetch(stats_cmd, "比赛数据", latest())
        if stats is None:
            await stats_cmd.finish("暂无比赛数据")

    await stats_cmd.finish(image_segment(await render_stats(stats)))


# 群开关
hltv_toggle = on_command(
    "hltv开启",
    aliases={"hltv关闭", "hltv启用", "hltv禁用"},
    rule=command_boundary,
    permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER,
    priority=5,
    block=True,
)


@hltv_toggle.handle()
async def handle_hltv_toggle(event: GroupMessageEvent):
    raw_cmd = event.get_plaintext().strip()
    enabled = "开启" in raw_cmd or "启用" in raw_cmd
    try:
        data_manager.set_enabled(event.group_id, enabled)
    except OSError:
        logger.exception("HLTV 群开关保存失败")
        await hltv_toggle.finish("修改未完成：保存数据失败")
    hltv_scheduler.sync_events(wake=enabled)
    await hltv_toggle.finish(
        "✅ HLTV 订阅功能已开启" if enabled else "❌ HLTV 订阅功能已关闭"
    )
