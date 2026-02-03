"""HLTV 数据源 - 使用 curl_cffi 绕过 Cloudflare

说明：
- 作为“门面（Facade）”对外提供同名方法
- 网络请求：由 HLTVHttpClient 负责（重试/代理/会话管理）
- HTML 解析：拆分到 plugins/hltv_sub/parsers/*
- 数据模型：拆分到 plugins/hltv_sub/models.py
"""

from __future__ import annotations

from typing import Optional

import pytz
from bs4 import BeautifulSoup
from nonebot.log import logger

from .config import plugin_config
from .http_client import HLTVHttpClient
from .models import (
    EventInfo,
    MatchInfo,
    MatchStats,
    MatchTimeHint,
    ResultInfo,
)
from .parsers.events import parse_big_events, parse_event_info
from .parsers.matches import parse_event_matches_with_hints
from .parsers.results import parse_event_results
from .parsers.stats import parse_match_stats


class HLTVDataSource:
    """HLTV 数据源"""

    BASE_URL = "https://www.hltv.org"

    def __init__(self):
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._client = HLTVHttpClient(
            timeout=plugin_config.hltv_timeout,
            min_delay=plugin_config.hltv_min_delay,
            proxy_list=plugin_config.hltv_proxy_list,
        )

    async def close(self):
        """关闭会话"""
        await self._client.close()

    async def get_big_events(self) -> list[EventInfo]:
        """获取 Big Events（正在进行 + 即将举行的赛事）"""
        html = await self._client.fetch(f"{self.BASE_URL}/events")
        if not html:
            return []
        return parse_big_events(html, self._tz)

    async def get_event_info(self, event_id: str, event_title: str = "") -> Optional[EventInfo]:
        """获取赛事详细信息"""
        title_slug = event_title.lower().replace(" ", "-") if event_title else "event"
        url = f"{self.BASE_URL}/events/{event_id}/{title_slug}"

        html = await self._client.fetch(url)
        if not html:
            return None

        return parse_event_info(html, event_id=event_id, event_title=event_title, tz=self._tz)

    async def get_event_matches_with_hints(
        self, event_id: str, days: int = 7
    ) -> tuple[list[MatchInfo], list[MatchTimeHint]]:
        """获取赛事比赛列表 + 时间提示（单次 fetch）

        - matches：过滤 TBD（用于渲染/提醒）
        - hints：不过滤 TBD（用于 scheduler 自适应轮询）
        """
        url = f"{self.BASE_URL}/events/{event_id}/matches"
        html = await self._client.fetch(url)
        if not html:
            return [], []

        soup = BeautifulSoup(html, "lxml")
        matches, hints = parse_event_matches_with_hints(soup, self._tz)

        logger.info(
            f"[HLTV] 获取到 {len(matches)} 场比赛 (filtered) / {len(hints)} 条时间提示 (raw)"
        )
        return matches, hints

    async def get_event_matches(self, event_id: str, days: int = 7) -> list[MatchInfo]:
        """获取赛事的比赛列表（过滤 TBD）"""
        matches, _ = await self.get_event_matches_with_hints(event_id, days=days)
        return matches

    async def get_event_results(
        self, event_id: str, days: int = 7, max_results: int = 20
    ) -> list[ResultInfo]:
        """获取赛事的已结束比赛结果"""
        url = f"{self.BASE_URL}/results?event={event_id}"
        html = await self._client.fetch(url)
        if not html:
            return []
        return parse_event_results(html, max_results=max_results)

    async def get_match_stats(
        self, match_id: str, team1: str = "", team2: str = "", event_title: str = ""
    ) -> Optional[MatchStats]:
        """获取比赛详细数据"""
        # 构建 URL（保持旧逻辑：slug 仅用于 URL 友好，不影响 match_id 定位）
        t1_slug = team1.lower().replace(" ", "-") if team1 else "team1"
        t2_slug = team2.lower().replace(" ", "-") if team2 else "team2"
        event_slug = event_title.lower().replace(" ", "-") if event_title else "event"

        url = f"{self.BASE_URL}/matches/{match_id}/{t1_slug}-vs-{t2_slug}-{event_slug}"
        logger.info(f"[HLTV] 获取比赛数据: {url}")

        html = await self._client.fetch(url)
        if not html:
            return None

        return parse_match_stats(
            html,
            match_id=match_id,
            team1=team1,
            team2=team2,
            event_title=event_title,
        )

    async def get_latest_result_with_stats(
        self, event_id: str, event_title: str = ""
    ) -> Optional[MatchStats]:
        """获取最近一场比赛的详细数据"""
        results = await self.get_event_results(event_id, max_results=1)
        if not results:
            logger.warning(f"[HLTV] 没有找到赛事 {event_id} 的比赛结果")
            return None

        result = results[0]
        logger.info(f"[HLTV] 获取最近比赛: {result.team1} vs {result.team2}")

        return await self.get_match_stats(
            result.id,
            team1=result.team1,
            team2=result.team2,
            event_title=event_title,
        )


# 全局实例
hltv_data = HLTVDataSource()
