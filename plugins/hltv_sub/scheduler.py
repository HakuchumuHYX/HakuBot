"""HLTV 定时推送模块"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Callable, TypeVar
from dataclasses import dataclass
import pytz

from nonebot import get_bot, get_driver, require
from nonebot.log import logger
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import plugin_config
from .data_manager import data_manager
from .data_source import hltv_data, MatchInfo
from .render import render_stats

T = TypeVar('T')


@dataclass
class UpcomingMatch:
    """即将开始的比赛信息"""
    match_id: str
    team1: str
    team2: str
    event_id: str
    event_title: str
    start_time: datetime
    minutes_until: int
    maps: str = ""


class HLTVScheduler:
    """HLTV 定时任务调度器"""
    
    def __init__(self):
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._running = False
        self._initialized = False
    
    async def _fetch_with_retry(
        self, 
        coro_func: Callable[[], T], 
        max_retries: int = 3, 
        delay: float = 2.0
    ) -> Optional[T]:
        """带重试的异步请求
        
        Args:
            coro_func: 返回协程的函数
            max_retries: 最大重试次数
            delay: 重试延迟基数（秒）
            
        Returns:
            请求结果，失败返回 None
        """
        for attempt in range(max_retries):
            try:
                return await coro_func()
            except FinishedException:
                raise
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"[HLTV Scheduler] 请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                    return None
                logger.warning(f"[HLTV Scheduler] 请求失败 (尝试 {attempt + 1}/{max_retries}): {e}，{delay * (attempt + 1)}秒后重试")
                await asyncio.sleep(delay * (attempt + 1))
        return None
    
    async def init_existing_results(self) -> int:
        """启动时初始化，将所有现有结果标记为已推送，避免重启后误推送
        
        Returns:
            标记的结果数量
        """
        if self._initialized:
            return 0
        
        event_ids = data_manager.get_all_subscribed_event_ids()
        
        if not event_ids:
            self._initialized = True
            return 0
        
        count = 0
        for event_id in event_ids:
            try:
                results = await self._fetch_with_retry(
                    lambda eid=event_id: hltv_data.get_event_results(eid, max_results=10)
                )
                
                if results:
                    for result in results:
                        if not data_manager.is_result_notified(result.id):
                            data_manager.add_notified_result(result.id)
                            count += 1
                            
            except FinishedException:
                raise
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 初始化赛事 {event_id} 结果失败: {e}")
                continue
        
        self._initialized = True
        logger.info(f"[HLTV Scheduler] 已初始化 {count} 条历史结果记录")
        return count
    
    async def check_match_starts(self) -> list[UpcomingMatch]:
        """检查即将开始的比赛，返回需要提醒的比赛列表"""
        upcoming = []
        now = datetime.now(self._tz)
        
        # 获取所有订阅的赛事
        event_ids = data_manager.get_all_subscribed_event_ids()
        
        if not event_ids:
            return upcoming
        
        # 获取赛事标题映射
        event_titles = {}
        for group in data_manager._groups.values():
            for event in group.subscribed_events:
                event_titles[event.event_id] = event.event_title
        
        for event_id in event_ids:
            try:
                matches = await self._fetch_with_retry(
                    lambda eid=event_id: hltv_data.get_event_matches(eid)
                )
                
                if not matches:
                    continue
                
                for match in matches:
                    if match.is_live:
                        continue
                    
                    # 解析比赛时间
                    match_time = self._parse_match_time(match.date, match.time)
                    if not match_time:
                        continue
                    
                    # 计算距离开始还有多少分钟
                    time_diff = match_time - now
                    minutes_until = int(time_diff.total_seconds() / 60)
                    
                    # 检查是否在提醒窗口内（12-17分钟，中心15分钟，给5分钟的轮询窗口）
                    if 12 <= minutes_until <= 17:
                        # 检查是否已经提醒过
                        if not data_manager.is_start_notified(match.id):
                            upcoming.append(UpcomingMatch(
                                match_id=match.id,
                                team1=match.team1,
                                team2=match.team2,
                                event_id=event_id,
                                event_title=event_titles.get(event_id, f"Event #{event_id}"),
                                start_time=match_time,
                                minutes_until=minutes_until,
                                maps=match.maps
                            ))
                            
            except FinishedException:
                raise
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 比赛失败: {e}")
                continue
        
        return upcoming
    
    async def check_match_results(self) -> list[tuple[str, str, str]]:
        """检查已结束的比赛，返回 [(event_id, event_title, match_id), ...]"""
        new_results = []
        
        # 获取所有订阅的赛事
        event_ids = data_manager.get_all_subscribed_event_ids()
        
        if not event_ids:
            return new_results
        
        # 获取赛事标题映射
        event_titles = {}
        for group in data_manager._groups.values():
            for event in group.subscribed_events:
                event_titles[event.event_id] = event.event_title
        
        for event_id in event_ids:
            try:
                results = await self._fetch_with_retry(
                    lambda eid=event_id: hltv_data.get_event_results(eid, max_results=5)
                )
                
                if not results:
                    continue
                
                for result in results:
                    # 检查是否已经推送过
                    if not data_manager.is_result_notified(result.id):
                        new_results.append((
                            event_id,
                            event_titles.get(event_id, f"Event #{event_id}"),
                            result.id
                        ))
                        
            except FinishedException:
                raise
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 结果失败: {e}")
                continue
        
        return new_results
    
    def _parse_match_time(self, date_str: str, time_str: str) -> Optional[datetime]:
        """解析比赛时间"""
        try:
            if not date_str or not time_str:
                return None
            
            if date_str == "LIVE" or time_str == "LIVE":
                return None
            
            # 日期格式: MM-DD, 时间格式: HH:MM
            now = datetime.now(self._tz)
            month, day = map(int, date_str.split("-"))
            hour, minute = map(int, time_str.split(":"))
            
            # 构建完整时间
            year = now.year
            match_time = datetime(year, month, day, hour, minute, tzinfo=self._tz)
            
            # 如果时间已经过去很久，可能是明年的比赛
            if match_time < now - timedelta(days=30):
                match_time = datetime(year + 1, month, day, hour, minute, tzinfo=self._tz)
            
            return match_time
            
        except Exception:
            return None
    
    async def send_match_reminder(self, bot: Bot, match: UpcomingMatch) -> None:
        """发送比赛开始提醒"""
        # 获取订阅该赛事的群组
        groups = data_manager.get_groups_by_event(match.event_id)
        
        if not groups:
            return
        
        # 构建消息
        bo_text = f"BO{match.maps}" if match.maps else ""
        msg = f"""🔔 比赛即将开始

🏆 {match.event_title}

⏰ {match.minutes_until} 分钟后开始
🎮 {match.team1} vs {match.team2}
{f'📋 {bo_text}' if bo_text else ''}"""
        
        # 发送到各群组
        for group_id in groups:
            try:
                await bot.send_group_msg(group_id=group_id, message=msg.strip())
                logger.info(f"[HLTV Scheduler] 已发送比赛提醒到群 {group_id}: {match.team1} vs {match.team2}")
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 发送比赛提醒到群 {group_id} 失败: {e}")
        
        # 记录已提醒
        data_manager.add_notified_start(match.match_id)
    
    async def send_match_result(self, bot: Bot, event_id: str, event_title: str, match_id: str) -> None:
        """发送比赛结果"""
        # 获取订阅该赛事的群组
        groups = data_manager.get_groups_by_event(event_id)
        
        if not groups:
            return
        
        try:
            # 获取比赛结果（带重试）
            results = await self._fetch_with_retry(
                lambda: hltv_data.get_event_results(event_id, max_results=10)
            )
            
            # 找到对应的比赛
            result = None
            if results:
                for r in results:
                    if r.id == match_id:
                        result = r
                        break
            
            if not result:
                logger.warning(f"[HLTV Scheduler] 未找到比赛结果: {match_id}")
                data_manager.add_notified_result(match_id)
                return
            
            # 获取详细数据（带重试）
            stats = await self._fetch_with_retry(
                lambda: hltv_data.get_match_stats(
                    match_id=match_id,
                    team1=result.team1,
                    team2=result.team2,
                    event_title=event_title
                )
            )
            
            if stats:
                # 渲染图片
                img = await render_stats(stats)
                msg = MessageSegment.text("🏁 比赛已结束\n\n") + MessageSegment.image(img)
            else:
                # 无法获取详细数据，发送简单结果
                msg = f"""🏁 比赛已结束

🏆 {event_title}

{result.team1} {result.score1} - {result.score2} {result.team2}"""
            
            # 发送到各群组
            for group_id in groups:
                try:
                    await bot.send_group_msg(group_id=group_id, message=msg)
                    logger.info(f"[HLTV Scheduler] 已发送比赛结果到群 {group_id}: {result.team1} vs {result.team2}")
                except Exception as e:
                    logger.error(f"[HLTV Scheduler] 发送比赛结果到群 {group_id} 失败: {e}")
            
        except Exception as e:
            logger.error(f"[HLTV Scheduler] 处理比赛结果 {match_id} 失败: {e}")
        
        # 记录已推送
        data_manager.add_notified_result(match_id)
    
    async def run_check(self) -> dict:
        """执行一次检查，返回检查结果"""
        result = {
            "upcoming_matches": [],
            "new_results": [],
            "errors": []
        }
        
        try:
            # 获取 bot
            bot = None
            try:
                bot = get_bot()
            except Exception:
                logger.debug("[HLTV Scheduler] 无法获取 Bot，跳过推送")
                return result
            
            # 检查即将开始的比赛
            upcoming = await self.check_match_starts()
            result["upcoming_matches"] = upcoming
            
            for match in upcoming:
                await self.send_match_reminder(bot, match)
            
            # 检查已结束的比赛
            new_results = await self.check_match_results()
            result["new_results"] = new_results
            
            for event_id, event_title, match_id in new_results:
                await self.send_match_result(bot, event_id, event_title, match_id)
            
            logger.info(f"[HLTV Scheduler] 检查完成: {len(upcoming)} 场即将开始, {len(new_results)} 场新结果")
            
        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查失败: {e}")
            result["errors"].append(str(e))
        
        return result
    
    async def get_upcoming_info(self) -> list[UpcomingMatch]:
        """获取所有即将开始的比赛信息（用于测试命令）"""
        upcoming = []
        now = datetime.now(self._tz)
        
        # 获取所有订阅的赛事
        event_ids = data_manager.get_all_subscribed_event_ids()
        
        if not event_ids:
            return upcoming
        
        # 获取赛事标题映射
        event_titles = {}
        for group in data_manager._groups.values():
            for event in group.subscribed_events:
                event_titles[event.event_id] = event.event_title
        
        for event_id in event_ids:
            try:
                matches = await hltv_data.get_event_matches(event_id)
                
                for match in matches:
                    if match.is_live:
                        continue
                    
                    # 解析比赛时间
                    match_time = self._parse_match_time(match.date, match.time)
                    if not match_time:
                        continue
                    
                    # 计算距离开始还有多少分钟
                    time_diff = match_time - now
                    minutes_until = int(time_diff.total_seconds() / 60)
                    
                    # 只显示未来的比赛
                    if minutes_until > 0:
                        upcoming.append(UpcomingMatch(
                            match_id=match.id,
                            team1=match.team1,
                            team2=match.team2,
                            event_id=event_id,
                            event_title=event_titles.get(event_id, f"Event #{event_id}"),
                            start_time=match_time,
                            minutes_until=minutes_until,
                            maps=match.maps
                        ))
                            
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 获取赛事 {event_id} 比赛失败: {e}")
                continue
        
        # 按开始时间排序
        upcoming.sort(key=lambda x: x.start_time)
        
        return upcoming


# 全局调度器实例
hltv_scheduler = HLTVScheduler()


# 注册定时任务
@scheduler.scheduled_job("interval", minutes=5, id="hltv_check")
async def scheduled_check():
    """每 5 分钟执行一次检查"""
    await hltv_scheduler.run_check()


# 启动时执行一次
driver = get_driver()

@driver.on_startup
async def on_startup():
    logger.info("[HLTV Scheduler] 定时任务已启动，间隔 5 分钟")
    # 延迟初始化，等待 bot 连接
    asyncio.create_task(_delayed_init())


async def _delayed_init():
    """延迟初始化，等待一段时间后再执行"""
    # 等待 10 秒，确保 bot 已连接
    await asyncio.sleep(10)
    try:
        count = await hltv_scheduler.init_existing_results()
        if count > 0:
            logger.info(f"[HLTV Scheduler] 启动初始化完成，标记了 {count} 条历史结果")
    except Exception as e:
        logger.error(f"[HLTV Scheduler] 启动初始化失败: {e}")
