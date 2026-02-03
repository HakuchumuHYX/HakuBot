"""
赛事相关命令：event列表 / event订阅 / event取消订阅 / 我的订阅
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

from ..data_manager import EventSubscription, data_manager
from ..data_source import hltv_data
from ..permissions import check_permission, is_group_enabled
from ..render import render_events


# event列表命令
event_list = on_command("event列表", aliases={"赛事列表", "events"}, priority=5, block=True)


@event_list.handle()
async def handle_event_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    await event_list.send("正在获取赛事列表，请稍候...")

    try:
        events = await hltv_data.get_big_events()

        if not events:
            await event_list.finish("暂无赛事数据")
            return

        ongoing = [e for e in events if e.is_ongoing]
        upcoming = [e for e in events if not e.is_ongoing]

        subscribed_ids = data_manager.get_subscribed_event_ids(group_id)

        img = await render_events(ongoing, upcoming, subscribed_ids)
        await event_list.finish(MessageSegment.image(img))

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"获取赛事列表失败: {e}")
        await event_list.finish("获取赛事列表失败，HLTV 可能暂时无法访问")


# event订阅命令
event_subscribe = on_command("event订阅", aliases={"订阅赛事", "subscribe"}, priority=5, block=True)


@event_subscribe.handle()
async def handle_event_subscribe(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    user_id = event.user_id

    if not is_group_enabled(group_id):
        return

    if not await check_permission(bot, group_id, user_id):
        await event_subscribe.finish("❌ 只有群主或管理员可以订阅赛事")
        return

    event_id = args.extract_plain_text().strip()
    if not event_id:
        await event_subscribe.finish("请提供赛事ID，例如：event订阅 7148")
        return

    # 检查是否已订阅
    if data_manager.is_subscribed(group_id, event_id):
        await event_subscribe.finish(f"已经订阅了赛事 #{event_id}")
        return

    await event_subscribe.send("正在获取赛事信息...")

    try:
        events = await hltv_data.get_big_events()
        event_info = None
        for e in events:
            if e.id == event_id:
                event_info = e
                break

        if not event_info:
            event_info = await hltv_data.get_event_info(event_id)

        if event_info:
            # 单订阅模式：订阅新赛事时覆盖旧订阅（内部会清空旧赛事的推送去重状态）
            data_manager.replace_subscriptions(
                group_id=group_id,
                subscription=EventSubscription(
                    event_id=event_id,
                    event_title=event_info.title,
                    start_date=event_info.start_date,
                    end_date=event_info.end_date,
                ),
            )

            # 只有进行中赛事才恢复定时任务；并先标记现有 results，避免订阅后立刻推历史结果
            from ..scheduler import hltv_scheduler

            if event_info.is_ongoing:
                await hltv_scheduler.initialize_event_results_as_notified(event_id)
            hltv_scheduler.ensure_job_state()

            await event_subscribe.finish(f"✅ 成功订阅赛事：{event_info.title}")
        else:
            # 未获取到详细信息：仍允许订阅，但不自动恢复定时任务
            data_manager.replace_subscriptions(
                group_id=group_id,
                subscription=EventSubscription(
                    event_id=event_id,
                    event_title=f"Event #{event_id}",
                    start_date="",
                    end_date="",
                ),
            )

            from ..scheduler import hltv_scheduler

            hltv_scheduler.ensure_job_state()

            await event_subscribe.finish(f"✅ 成功订阅赛事 #{event_id}")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"订阅赛事失败: {e}")
        await event_subscribe.finish("订阅失败，HLTV 可能暂时无法访问")


# event取消订阅命令
event_unsubscribe = on_command("event取消订阅", aliases={"取消订阅赛事", "unsubscribe"}, priority=5, block=True)


@event_unsubscribe.handle()
async def handle_event_unsubscribe(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    user_id = event.user_id

    if not is_group_enabled(group_id):
        return

    if not await check_permission(bot, group_id, user_id):
        await event_unsubscribe.finish("❌ 只有群主或管理员可以取消订阅")
        return

    event_id = args.extract_plain_text().strip()
    if not event_id:
        await event_unsubscribe.finish("请提供赛事ID，例如：event取消订阅 7148")
        return

    if data_manager.unsubscribe_event(group_id, event_id):
        from ..scheduler import hltv_scheduler

        hltv_scheduler.ensure_job_state()
        await event_unsubscribe.finish(f"✅ 已取消订阅赛事 #{event_id}")
    else:
        await event_unsubscribe.finish(f"未订阅赛事 #{event_id}")


# 我的订阅命令
my_subscriptions = on_command("我的订阅", aliases={"订阅列表", "mysub"}, priority=5, block=True)


@my_subscriptions.handle()
async def handle_my_subscriptions(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    subscriptions = data_manager.get_subscribed_events(group_id)
    if not subscriptions:
        await my_subscriptions.finish("当前没有订阅任何赛事\n使用 event列表 查看可订阅的赛事")
        return

    msg = "📋 已订阅的赛事：\n"
    for sub in subscriptions:
        msg += f"• #{sub.event_id} {sub.event_title}\n"
        if sub.start_date and sub.end_date:
            msg += f"  📅 {sub.start_date} ~ {sub.end_date}\n"

    await my_subscriptions.finish(msg.strip())
