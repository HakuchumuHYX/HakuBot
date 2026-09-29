"""HLTV 群命令、权限检查与回复。"""

from __future__ import annotations

import math
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

from utils.onebot.media import image_segment
from plugins.hltv_sub.client import HLTVFetchError, hltv_client
from plugins.hltv_sub.data_manager import EventSubscription, data_manager
from plugins.hltv_sub.handler import hltv_handler
from plugins.hltv_sub.help_content import build_help_sections
from plugins.hltv_sub.render import (
    render_events, render_help, render_matches, render_results, render_stats,
)
from plugins.hltv_sub.scheduler import hltv_scheduler


async def check_permission(bot: Bot, group_id: int, user_id: int) -> bool:
    """检查权限：群主、管理员或超级用户"""
    superusers = getattr(bot.config, "superusers", set())
    if str(user_id) in superusers:
        return True

    try:
        member_info = await bot.get_group_member_info(
            group_id=group_id, user_id=user_id
        )
        return member_info.get("role") in ("owner", "admin")
    except Exception:
        return False


# 赛事管理
# event列表命令
event_list = on_command(
    "event列表", aliases={"赛事列表", "events"}, priority=5, block=True
)


@event_list.handle()
async def handle_event_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not data_manager.is_enabled(group_id):
        return

    await event_list.send("正在获取赛事列表，请稍候...")

    try:
        events = await hltv_client.get_big_events()

        if not events:
            await event_list.finish("暂无赛事数据")
            return

        ongoing = [e for e in events if e.is_ongoing]
        upcoming = [e for e in events if not e.is_ongoing]

        subscribed_ids = list(data_manager.get_all_subscribed_event_ids())

        img = await render_events(ongoing, upcoming, subscribed_ids)
        await event_list.finish(image_segment(img))

    except FinishedException:
        raise
    except HLTVFetchError as e:
        await event_list.finish(str(e))
    except Exception as e:
        logger.error(f"获取赛事列表失败: {e}")
        await event_list.finish("获取赛事列表失败，HLTV 可能暂时无法访问")


# event订阅命令
event_subscribe = on_command(
    "event订阅", aliases={"订阅赛事", "subscribe"}, priority=5, block=True
)


@event_subscribe.handle()
async def handle_event_subscribe(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = event.group_id
    user_id = event.user_id

    if not data_manager.is_enabled(group_id):
        return

    if not await check_permission(bot, group_id, user_id):
        await event_subscribe.finish("❌ 只有群主或管理员可以订阅赛事")
        return

    event_id = args.extract_plain_text().strip()
    if not event_id:
        await event_subscribe.finish("请提供赛事ID，例如：event订阅 7148")
        return

    # 全局同步多订阅：若该赛事已在全局订阅中，直接提示
    if data_manager.is_subscribed(event_id):
        await event_subscribe.finish(f"已经订阅了赛事 #{event_id}")
        return

    await event_subscribe.send("正在获取赛事信息...")

    try:
        events = await hltv_client.get_big_events()
        event_info = None
        for e in events:
            if e.id == event_id:
                event_info = e
                break

        if not event_info or not event_info.start_date or not event_info.end_date:
            event_info = await hltv_client.get_event_info(event_id)

        if (
            event_info and event_info.title
            and event_info.start_date and event_info.end_date
        ):
            # 在写入订阅前完成网络操作，避免失败后留下半完成订阅。
            if event_info.is_ongoing:
                await hltv_handler.initialize_event_results_as_notified(event_id)

            created = data_manager.subscribe_event(
                subscription=EventSubscription(
                    event_id=event_id,
                    event_title=event_info.title,
                    start_date=event_info.start_date,
                    end_date=event_info.end_date,
                ),
            )
            if not created:
                await event_subscribe.finish(f"已经订阅了赛事 #{event_id}")
                return

            hltv_scheduler.sync_events()

            await event_subscribe.finish(f"✅ 成功订阅赛事：{event_info.title}")
        else:
            await event_subscribe.finish(f"无法获取赛事 #{event_id} 的信息，未创建订阅")

    except FinishedException:
        raise
    except OSError:
        logger.exception("订阅时保存数据失败")
        await event_subscribe.finish("订阅未完成：保存数据失败，请查看日志")
    except HLTVFetchError as e:
        await event_subscribe.finish(f"订阅未完成：{e}")
    except Exception as e:
        logger.error(f"订阅赛事失败: {e}")
        await event_subscribe.finish("订阅失败，HLTV 可能暂时无法访问")


# event取消订阅命令
event_unsubscribe = on_command(
    "event取消订阅", aliases={"取消订阅赛事", "unsubscribe"}, priority=5, block=True
)


@event_unsubscribe.handle()
async def handle_event_unsubscribe(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = event.group_id
    user_id = event.user_id

    if not data_manager.is_enabled(group_id):
        return

    if not await check_permission(bot, group_id, user_id):
        await event_unsubscribe.finish("❌ 只有群主或管理员可以取消订阅")
        return

    event_id = args.extract_plain_text().strip()
    if not event_id:
        await event_unsubscribe.finish("请提供赛事ID，例如：event取消订阅 7148")
        return

    try:
        removed = data_manager.unsubscribe_event(event_id)
    except OSError:
        logger.exception("取消订阅时保存数据失败")
        await event_unsubscribe.finish("取消订阅未完成：保存数据失败")
        return
    if removed:
        hltv_scheduler.sync_events()
        await event_unsubscribe.finish(f"✅ 已取消订阅赛事 #{event_id}")
    else:
        await event_unsubscribe.finish(f"未订阅赛事 #{event_id}")


# 我的订阅命令
my_subscriptions = on_command(
    "我的订阅", aliases={"订阅列表", "mysub"}, priority=5, block=True
)


@my_subscriptions.handle()
async def handle_my_subscriptions(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not data_manager.is_enabled(group_id):
        return

    subscriptions = data_manager.get_subscribed_events()
    if not subscriptions:
        await my_subscriptions.finish(
            "当前没有订阅任何赛事\n使用 event列表 查看可订阅的赛事"
        )
        return

    msg = "📋 已订阅的赛事：\n"
    for sub in subscriptions:
        msg += f"• #{sub.event_id} {sub.event_title}\n"
        if sub.start_date and sub.end_date:
            msg += f"  📅 {sub.start_date[5:]} ~ {sub.end_date[5:]}\n"

    await my_subscriptions.finish(msg.strip())


# 比赛列表
matches_list = on_command(
    "matches列表", aliases={"比赛列表", "matches"}, priority=5, block=True
)


@matches_list.handle()
async def handle_matches_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not data_manager.is_enabled(group_id):
        return

    subscriptions = data_manager.get_subscribed_events()
    if not subscriptions:
        await matches_list.finish("请先订阅赛事\n使用 event列表 查看可订阅的赛事")
        return

    await matches_list.send("正在获取比赛列表，请稍候...")

    try:
        matches_by_event = {}
        live_count = 0
        upcoming_count = 0

        for sub in sorted(
            subscriptions,
            key=lambda x: int(x.event_id) if x.event_id.isdigit() else x.event_id,
        ):
            page = await hltv_client.get_event_matches(sub.event_id, include_partial_tbd=True)
            matches = page.matches

            if matches:
                event_key = f"#{sub.event_id} {sub.event_title}"
                matches_by_event[event_key] = matches
                for m in matches:
                    if m.is_live:
                        live_count += 1
                    else:
                        upcoming_count += 1

        if not matches_by_event:
            await matches_list.finish("暂无比赛")
            return

        img = await render_matches(matches_by_event, live_count, upcoming_count)
        await matches_list.finish(image_segment(img))

    except FinishedException:
        raise
    except HLTVFetchError as e:
        await matches_list.finish(str(e))
    except Exception as e:
        logger.error(f"获取比赛列表失败: {e}")
        await matches_list.finish("获取比赛列表失败，HLTV 可能暂时无法访问")


# 比赛结果
results_list = on_command(
    "results列表", aliases={"结果列表", "results"}, priority=5, block=True
)


@results_list.handle()
async def handle_results_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not data_manager.is_enabled(group_id):
        return

    subscriptions = data_manager.get_subscribed_events()
    if not subscriptions:
        await results_list.finish("请先订阅赛事\n使用 event列表 查看可订阅的赛事")
        return

    await results_list.send("正在获取比赛结果，请稍候...")

    try:
        results_by_event = {}

        for sub in sorted(
            subscriptions,
            key=lambda x: int(x.event_id) if x.event_id.isdigit() else x.event_id,
        ):
            results = await hltv_client.get_event_results(sub.event_id)
            if results:
                event_key = f"#{sub.event_id} {sub.event_title}"
                results_by_event[event_key] = results

        if not results_by_event:
            await results_list.finish("暂无比赛结果")
            return

        img = await render_results(results_by_event)
        await results_list.finish(image_segment(img))

    except FinishedException:
        raise
    except HLTVFetchError as e:
        await results_list.finish(str(e))
    except Exception as e:
        logger.error(f"获取比赛结果失败: {e}")
        await results_list.finish("获取比赛结果失败，HLTV 可能暂时无法访问")


# 比赛数据
stats_cmd = on_command("stats", priority=5, block=True)


@stats_cmd.handle()
async def handle_stats(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = event.group_id

    if not data_manager.is_enabled(group_id):
        return

    match_id = args.extract_plain_text().strip()
    subscriptions = data_manager.get_subscribed_events()

    if not match_id:
        # 获取最新比赛数据
        if not subscriptions:
            await stats_cmd.finish("请先订阅赛事，或提供比赛ID\n例如：stats 2370931")
            return

        await stats_cmd.send("正在获取最新比赛数据...")

        try:
            for sub in subscriptions:
                stats = await hltv_client.get_latest_result_with_stats(
                    sub.event_id, sub.event_title
                )
                if stats:
                    img = await render_stats(stats)
                    await stats_cmd.finish(image_segment(img))
                    return

            await stats_cmd.finish("暂无比赛数据")

        except FinishedException:
            raise
        except HLTVFetchError as e:
            await stats_cmd.finish(str(e))
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish("获取比赛数据失败，HLTV 可能暂时无法访问")

    else:
        # 获取指定比赛数据
        await stats_cmd.send(f"正在获取比赛 #{match_id} 的数据...")

        try:
            stats = await hltv_client.get_match_stats(match_id=match_id)

            if stats:
                img = await render_stats(stats)
                await stats_cmd.finish(image_segment(img))
            else:
                await stats_cmd.finish(f"无法获取比赛 #{match_id} 的数据")

        except FinishedException:
            raise
        except HLTVFetchError as e:
            await stats_cmd.finish(str(e))
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish("获取比赛数据失败，HLTV 可能暂时无法访问")


# 群开关
hltv_toggle = on_command(
    "hltv开启",
    aliases={"hltv关闭", "hltv启用", "hltv禁用"},
    priority=5,
    block=True,
)


@hltv_toggle.handle()
async def handle_hltv_toggle(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    user_id = event.user_id

    if not await check_permission(bot, group_id, user_id):
        await hltv_toggle.finish("❌ 需要管理员权限")
        return

    raw_cmd = event.get_plaintext().strip()

    enabled = "开启" in raw_cmd or "启用" in raw_cmd
    try:
        data_manager.set_enabled(group_id, enabled)
    except OSError:
        logger.exception("HLTV 群开关保存失败")
        await hltv_toggle.finish("修改未完成：保存数据失败")
        return
    hltv_scheduler.sync_events(wake=enabled)
    await hltv_toggle.finish(
        "✅ HLTV 订阅功能已开启" if enabled else "❌ HLTV 订阅功能已关闭"
    )


# 超级用户调试
hltv_check = on_command("hltv_check", priority=1, block=True)


@hltv_check.handle()
async def handle_hltv_check(bot: Bot, event: GroupMessageEvent):
    user_id = str(event.user_id)

    superusers = getattr(bot.config, "superusers", set())
    if user_id not in superusers:
        return

    await hltv_check.send("正在检查即将开始的比赛...")

    try:
        upcoming = await hltv_handler.get_upcoming_info()

        if not upcoming:
            await hltv_check.finish("暂无即将开始的比赛")
            return

        msg = "📋 即将开始的比赛：\n\n"

        for match in upcoming[:10]:
            minutes_until = math.ceil(
                (match.start_time - datetime.now(match.start_time.tzinfo)).total_seconds() / 60
            )
            if minutes_until >= 60:
                hours = minutes_until // 60
                mins = minutes_until % 60
                time_str = f"{hours}小时{mins}分钟" if mins > 0 else f"{hours}小时"
            else:
                time_str = f"{minutes_until}分钟"

            bo_text = f"BO{match.maps}" if match.maps else ""
            notified = "✓" if data_manager.is_start_notified(match.match_id) else ""

            msg += f"⏰ {time_str}后 {notified}\n"
            msg += f"🎮 {match.team1} vs {match.team2}\n"
            msg += f"🏆 {match.event_title}"
            if bo_text:
                msg += f" | {bo_text}"
            msg += "\n\n"

        if len(upcoming) > 10:
            msg += f"... 还有 {len(upcoming) - 10} 场比赛"

        await hltv_check.finish(msg.strip())

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"检查比赛失败: {e}")
        await hltv_check.finish(f"检查失败: {e}")


hltv_trigger = on_command("hltv_trigger", priority=1, block=True)


@hltv_trigger.handle()
async def handle_hltv_trigger(bot: Bot, event: GroupMessageEvent):
    user_id = str(event.user_id)

    superusers = getattr(bot.config, "superusers", set())
    if user_id not in superusers:
        return

    await hltv_trigger.send("正在手动执行定时任务检查...")

    try:
        result = await hltv_scheduler.run_check()

        msg = "📊 检查结果：\n\n"
        msg += f"开赛候选：{result['start_candidates']} 场\n"
        msg += f"单图候选：{result['map_candidates']} 张\n"
        msg += f"整场候选：{result['result_candidates']} 场\n"
        msg += "以上为发现数量，不代表发送成功数。\n"

        if result["errors"]:
            msg += f"错误：{len(result['errors'])} 个\n"
            for err in result["errors"][:3]:
                msg += f"  - {err}\n"

        await hltv_trigger.finish(msg.strip())

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"手动触发检查失败: {e}")
        await hltv_trigger.finish(f"执行失败: {e}")


# 帮助
hltv_help = on_command("hltv帮助", aliases={"hltvhelp"}, priority=5, block=True)


@hltv_help.handle()
async def handle_hltv_help(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not data_manager.is_enabled(group_id):
        return

    img = await render_help(build_help_sections())
    await hltv_help.finish(image_segment(img))
