"""HLTV 订阅插件

提供 HLTV 赛事订阅和比赛信息查询功能
"""

from nonebot import on_command, require, get_driver
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message
from nonebot.log import logger
from nonebot.exception import FinishedException

require("nonebot_plugin_localstore")
require("nonebot_plugin_htmlrender")

from .config import Config
from .data_manager import data_manager
from .data_source import hltv_data, EventInfo
from .render import render_events, render_matches, render_results, render_stats

# 导入 scheduler 以注册定时任务
from . import scheduler


__plugin_meta__ = PluginMetadata(
    name="HLTV订阅",
    description="HLTV CS2 赛事订阅和比赛信息查询",
    usage="""命令列表：
- event列表：查看近期大型赛事
- event订阅 [ID]：订阅赛事
- event取消订阅 [ID]：取消订阅
- matches列表：查看已订阅赛事的比赛
- results列表：查看已订阅赛事的结果
- stats：查看最新比赛数据
- stats [ID]：查看指定比赛数据
""",
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

driver = get_driver()


# ==================== 辅助函数 ====================

def is_group_enabled(group_id: int) -> bool:
    """检查群组是否启用插件"""
    return data_manager.is_enabled(group_id)


# ==================== 命令处理 ====================

# event列表命令
event_list = on_command("event列表", aliases={"赛事列表", "events"}, priority=5, block=True)

@event_list.handle()
async def handle_event_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
        return
    
    await event_list.send("正在获取赛事列表，请稍候...")
    
    try:
        # 获取赛事列表
        events = await hltv_data.get_big_events()
        
        if not events:
            await event_list.finish("暂无赛事数据")
            return
        
        # 分类
        ongoing = [e for e in events if e.is_ongoing]
        upcoming = [e for e in events if not e.is_ongoing]
        
        # 获取已订阅的赛事ID
        subscribed_ids = data_manager.get_subscribed_event_ids(group_id)
        
        # 渲染图片
        img = await render_events(ongoing, upcoming, subscribed_ids)
        
        await event_list.finish(MessageSegment.image(img))
        
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"获取赛事列表失败: {e}")
        await event_list.finish(f"获取赛事列表失败，HLTV 可能暂时无法访问")


# event订阅命令
event_subscribe = on_command("event订阅", aliases={"订阅赛事", "subscribe"}, priority=5, block=True)

@event_subscribe.handle()
async def handle_event_subscribe(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
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
        # 尝试获取赛事信息
        events = await hltv_data.get_big_events()
        event_info = None
        
        for e in events:
            if e.id == event_id:
                event_info = e
                break
        
        if not event_info:
            # 尝试直接获取
            event_info = await hltv_data.get_event_info(event_id)
        
        if event_info:
            # 订阅
            data_manager.subscribe_event(
                group_id=group_id,
                event_id=event_id,
                event_title=event_info.title,
                start_date=event_info.start_date,
                end_date=event_info.end_date
            )
            await event_subscribe.finish(f"✅ 成功订阅赛事：{event_info.title}")
        else:
            # 没有获取到详细信息，但仍然允许订阅
            data_manager.subscribe_event(
                group_id=group_id,
                event_id=event_id,
                event_title=f"Event #{event_id}"
            )
            await event_subscribe.finish(f"✅ 成功订阅赛事 #{event_id}")
            
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"订阅赛事失败: {e}")
        await event_subscribe.finish(f"订阅失败，HLTV 可能暂时无法访问")


# event取消订阅命令
event_unsubscribe = on_command("event取消订阅", aliases={"取消订阅赛事", "unsubscribe"}, priority=5, block=True)

@event_unsubscribe.handle()
async def handle_event_unsubscribe(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
        return
    
    event_id = args.extract_plain_text().strip()
    
    if not event_id:
        await event_unsubscribe.finish("请提供赛事ID，例如：event取消订阅 7148")
        return
    
    # 取消订阅
    if data_manager.unsubscribe_event(group_id, event_id):
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


# matches列表命令
matches_list = on_command("matches列表", aliases={"比赛列表", "matches"}, priority=5, block=True)

@matches_list.handle()
async def handle_matches_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
        return
    
    subscriptions = data_manager.get_subscribed_events(group_id)
    
    if not subscriptions:
        await matches_list.finish("请先订阅赛事\n使用 event列表 查看可订阅的赛事")
        return
    
    await matches_list.send("正在获取比赛列表，请稍候...")
    
    try:
        matches_by_event = {}
        live_count = 0
        upcoming_count = 0
        
        for sub in subscriptions:
            matches = await hltv_data.get_event_matches(sub.event_id)
            
            if matches:
                matches_by_event[sub.event_title] = matches
                for m in matches:
                    if m.is_live:
                        live_count += 1
                    else:
                        upcoming_count += 1
        
        if not matches_by_event:
            await matches_list.finish("暂无比赛")
            return
        
        # 渲染图片
        img = await render_matches(matches_by_event, live_count, upcoming_count)
        
        await matches_list.finish(MessageSegment.image(img))
        
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"获取比赛列表失败: {e}")
        await matches_list.finish(f"获取比赛列表失败，HLTV 可能暂时无法访问")


# results列表命令
results_list = on_command("results列表", aliases={"结果列表", "results"}, priority=5, block=True)

@results_list.handle()
async def handle_results_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
        return
    
    subscriptions = data_manager.get_subscribed_events(group_id)
    
    if not subscriptions:
        await results_list.finish("请先订阅赛事\n使用 event列表 查看可订阅的赛事")
        return
    
    await results_list.send("正在获取比赛结果，请稍候...")
    
    try:
        results_by_event = {}
        
        for sub in subscriptions:
            results = await hltv_data.get_event_results(sub.event_id)
            
            if results:
                results_by_event[sub.event_title] = results
        
        if not results_by_event:
            await results_list.finish("暂无比赛结果")
            return
        
        # 渲染图片
        img = await render_results(results_by_event)
        
        await results_list.finish(MessageSegment.image(img))
        
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"获取比赛结果失败: {e}")
        await results_list.finish(f"获取比赛结果失败，HLTV 可能暂时无法访问")


# stats命令
stats_cmd = on_command("stats", aliases={"比赛数据", "数据"}, priority=5, block=True)

@stats_cmd.handle()
async def handle_stats(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
        return
    
    match_id = args.extract_plain_text().strip()
    
    subscriptions = data_manager.get_subscribed_events(group_id)
    
    if not match_id:
        # 获取最新比赛数据
        if not subscriptions:
            await stats_cmd.finish("请先订阅赛事，或提供比赛ID\n例如：stats 2370931")
            return
        
        await stats_cmd.send("正在获取最新比赛数据...")
        
        try:
            # 尝试从每个订阅的赛事获取最新结果
            for sub in subscriptions:
                stats = await hltv_data.get_latest_result_with_stats(
                    sub.event_id, 
                    sub.event_title
                )
                
                if stats:
                    img = await render_stats(stats)
                    await stats_cmd.finish(MessageSegment.image(img))
                    return
            
            await stats_cmd.finish("暂无比赛数据")
            
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish(f"获取比赛数据失败，HLTV 可能暂时无法访问")
    
    else:
        # 获取指定比赛数据
        await stats_cmd.send(f"正在获取比赛 #{match_id} 的数据...")
        
        try:
            # 先尝试从订阅的赛事中查找比赛信息
            team1 = ""
            team2 = ""
            event_title = ""
            
            for sub in subscriptions:
                results = await hltv_data.get_event_results(sub.event_id)
                for r in results:
                    if r.id == match_id:
                        team1 = r.team1
                        team2 = r.team2
                        event_title = sub.event_title
                        break
                if team1:
                    break
            
            stats = await hltv_data.get_match_stats(
                match_id=match_id,
                team1=team1,
                team2=team2,
                event_title=event_title
            )
            
            if stats:
                img = await render_stats(stats)
                await stats_cmd.finish(MessageSegment.image(img))
            else:
                await stats_cmd.finish(f"无法获取比赛 #{match_id} 的数据")
                
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish(f"获取比赛数据失败，HLTV 可能暂时无法访问")


# 启用/禁用插件命令（管理员）
hltv_toggle = on_command("hltv开启", aliases={"hltv关闭", "hltv启用", "hltv禁用"}, priority=5, block=True)

@hltv_toggle.handle()
async def handle_hltv_toggle(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    user_id = event.user_id
    
    # 检查是否是管理员
    member_info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
    role = member_info.get("role", "")
    
    if role not in ("owner", "admin"):
        await hltv_toggle.finish("需要管理员权限")
        return
    
    # 获取命令
    raw_cmd = event.get_plaintext().strip()
    
    if "开启" in raw_cmd or "启用" in raw_cmd:
        data_manager.set_enabled(group_id, True)
        await hltv_toggle.finish("✅ HLTV 订阅功能已开启")
    else:
        data_manager.set_enabled(group_id, False)
        await hltv_toggle.finish("❌ HLTV 订阅功能已关闭")


# 帮助命令
hltv_help = on_command("hltv帮助", aliases={"hltv", "hltvhelp"}, priority=5, block=True)

@hltv_help.handle()
async def handle_hltv_help(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    
    if not is_group_enabled(group_id):
        return
    
    help_msg = """📖 HLTV 订阅插件帮助

【赛事相关】
• event列表 - 查看近期大型赛事
• event订阅 [ID] - 订阅指定赛事
• event取消订阅 [ID] - 取消订阅
• 我的订阅 - 查看已订阅的赛事

【比赛相关】
• matches列表 - 查看已订阅赛事的比赛
• results列表 - 查看已订阅赛事的结果
• stats - 查看最新比赛数据
• stats [ID] - 查看指定比赛数据

【管理命令】
• hltv开启 - 开启本群功能
• hltv关闭 - 关闭本群功能"""
    
    await hltv_help.finish(help_msg)


# ==================== 超级用户测试命令 ====================

# 超级用户检查命令
hltv_check = on_command("hltv_check", priority=1, block=True)

@hltv_check.handle()
async def handle_hltv_check(bot: Bot, event: GroupMessageEvent):
    user_id = str(event.user_id)
    
    # 检查是否是超级用户
    superusers = getattr(bot.config, "superusers", set())
    if user_id not in superusers:
        return
    
    await hltv_check.send("正在检查即将开始的比赛...")
    
    try:
        from .scheduler import hltv_scheduler
        
        upcoming = await hltv_scheduler.get_upcoming_info()
        
        if not upcoming:
            await hltv_check.finish("暂无即将开始的比赛")
            return
        
        msg = "📋 即将开始的比赛：\n\n"
        
        for match in upcoming[:10]:  # 只显示前10场
            # 格式化剩余时间
            if match.minutes_until >= 60:
                hours = match.minutes_until // 60
                mins = match.minutes_until % 60
                time_str = f"{hours}小时{mins}分钟" if mins > 0 else f"{hours}小时"
            else:
                time_str = f"{match.minutes_until}分钟"
            
            bo_text = f"BO{match.maps}" if match.maps else ""
            notified = "✓" if data_manager.is_start_notified(match.match_id) else ""
            
            msg += f"⏰ {time_str}后 {notified}\n"
            msg += f"🎮 {match.team1} vs {match.team2}\n"
            msg += f"🏆 {match.event_title}"
            if bo_text:
                msg += f" | {bo_text}"
            msg += f"\n\n"
        
        if len(upcoming) > 10:
            msg += f"... 还有 {len(upcoming) - 10} 场比赛"
        
        await hltv_check.finish(msg.strip())
    
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"检查比赛失败: {e}")
        await hltv_check.finish(f"检查失败: {e}")


# 手动触发检查
hltv_trigger = on_command("hltv_trigger", priority=1, block=True)

@hltv_trigger.handle()
async def handle_hltv_trigger(bot: Bot, event: GroupMessageEvent):
    user_id = str(event.user_id)
    
    # 检查是否是超级用户
    superusers = getattr(bot.config, "superusers", set())
    if user_id not in superusers:
        return
    
    await hltv_trigger.send("正在手动执行定时任务检查...")
    
    try:
        from .scheduler import hltv_scheduler
        
        result = await hltv_scheduler.run_check()
        
        msg = "📊 检查结果：\n\n"
        msg += f"即将开始：{len(result['upcoming_matches'])} 场\n"
        msg += f"新结果：{len(result['new_results'])} 场\n"
        
        if result['errors']:
            msg += f"错误：{len(result['errors'])} 个\n"
            for err in result['errors'][:3]:
                msg += f"  - {err}\n"
        
        await hltv_trigger.finish(msg.strip())
    
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"手动触发检查失败: {e}")
        await hltv_trigger.finish(f"执行失败: {e}")


# 清理资源
@driver.on_shutdown
async def cleanup():
    await hltv_data.close()
    logger.info("HLTV 订阅插件已清理资源")
