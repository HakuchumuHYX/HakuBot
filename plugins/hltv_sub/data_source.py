"""HLTV 数据源 - 使用 curl_cffi 绕过 Cloudflare

说明：
- 作为“门面（Facade）”对外提供同名方法
- 网络请求：由 HLTVHttpClient 负责（重试/代理/会话管理）
- HTML 解析：拆分到 plugins/hltv_sub/parsers/*
- 数据模型：拆分到 plugins/hltv_sub/models.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytz
from bs4 import BeautifulSoup
from nonebot.log import logger

from .config import plugin_config
from .http_client import FetchResult, HLTVHttpClient
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


@dataclass
class EventMatchesMeta:
    status_code: Optional[int] = None
    final_url: str = ""
    page_title: str = ""
    match_wrapper_count: int = 0
    match_link_count: int = 0
    is_unavailable: bool = False
    unavailable_reason: str = ""


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

    def _analyze_matches_meta(
        self, event_id: str, fetch_result: FetchResult, soup: Optional[BeautifulSoup]
    ) -> EventMatchesMeta:
        status = fetch_result.status_code
        final_url = fetch_result.final_url or ""
        title = soup.title.get_text(strip=True) if soup and soup.title else ""

        wrappers = soup.find_all("div", class_="match-wrapper") if soup else []
        links = soup.find_all("a", href=lambda x: bool(x and "/matches/" in x)) if soup else []
        text = soup.get_text(" ", strip=True).lower() if soup else ""

        meta = EventMatchesMeta(
            status_code=status,
            final_url=final_url,
            page_title=title,
            match_wrapper_count=len(wrappers),
            match_link_count=len(links),
        )

        if status is None:
            return meta

        if status != 200:
            meta.is_unavailable = True
            meta.unavailable_reason = f"http_{status}"
            return meta

        expected_path = f"/events/{event_id}/matches"
        if final_url and expected_path not in final_url:
            meta.is_unavailable = True
            meta.unavailable_reason = "unexpected_final_url"
            return meta

        # 真实抓取证据：finished 赛事会返回通用 matches 页（标题固定 + 无 wrapper + no matches yet）
        is_generic_matches_title = "counter-strike matches & livescore" in title.lower()
        has_no_matches_marker = ("no matches yet" in text) or ("no matches" in text)

        if is_generic_matches_title and len(wrappers) == 0 and has_no_matches_marker:
            meta.is_unavailable = True
            meta.unavailable_reason = "generic_matches_page_no_event_matches"

        return meta

    async def get_event_matches_with_hints_and_meta(
        self, event_id: str, days: int = 7
    ) -> tuple[list[MatchInfo], list[MatchTimeHint], EventMatchesMeta]:
        """获取赛事比赛列表 + 时间提示 + 页面元信息"""
        url = f"{self.BASE_URL}/events/{event_id}/matches"
        fetch_result = await self._client.fetch_with_meta(url)
        if not fetch_result.text:
            meta = self._analyze_matches_meta(event_id, fetch_result, None)
            if not meta.unavailable_reason:
                meta.is_unavailable = False
                meta.unavailable_reason = "empty_response"
            return [], [], meta

        soup = BeautifulSoup(fetch_result.text, "lxml")
        meta = self._analyze_matches_meta(event_id, fetch_result, soup)

        # 页面不可用时，明确返回空，避免 parser fallback 误抓全站 /matches 链接
        if meta.is_unavailable:
            logger.warning(
                f"[HLTV] 赛事 matches 页面不可用: event={event_id}, "
                f"status={meta.status_code}, final_url={meta.final_url}, "
                f"title={meta.page_title}, reason={meta.unavailable_reason}"
            )
            return [], [], meta

        matches, hints = parse_event_matches_with_hints(soup, self._tz)
        logger.info(
            f"[HLTV] 获取到 {len(matches)} 场比赛 (filtered) / {len(hints)} 条时间提示 (raw), "
            f"event={event_id}, wrappers={meta.match_wrapper_count}"
        )
        return matches, hints, meta

    async def get_event_matches_with_hints(
        self, event_id: str, days: int = 7
    ) -> tuple[list[MatchInfo], list[MatchTimeHint]]:
        """获取赛事比赛列表 + 时间提示（单次 fetch）

        - matches：过滤 TBD（用于渲染/提醒）
        - hints：不过滤 TBD（用于 scheduler 自适应轮询）
        """
        matches, hints, _ = await self.get_event_matches_with_hints_and_meta(event_id, days=days)
        return matches, hints

    async def get_event_matches_health(self, event_id: str) -> EventMatchesMeta:
        """仅获取赛事 matches 页可用性信息（供 daily maintenance 判定）"""
        _, _, meta = await self.get_event_matches_with_hints_and_meta(event_id)
        return meta

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
            logger.warning(f"[HLTV][RESULTS] fetch_empty event={event_id} url={url}")
            return []

        results = parse_event_results(html, max_results=max_results)
        logger.info(
            f"[HLTV][RESULTS] parsed event={event_id} count={len(results)} max_results={max_results}"
        )
        return results

    async def get_match_stats(
        self, match_id: str, team1: str = "", team2: str = "", event_title: str = ""
    ) -> Optional[MatchStats]:
        """获取比赛详细数据"""
        # 构建 URL（保持旧逻辑：slug 仅用于 URL 友好，不影响 match_id 定位）
        t1_slug = team1.lower().replace(" ", "-") if team1 else "team1"
        t2_slug = team2.lower().replace(" ", "-") if team2 else "team2"
        event_slug = event_title.lower().replace(" ", "-") if event_title else "event"

        url = f"{self.BASE_URL}/matches/{match_id}/{t1_slug}-vs-{t2_slug}-{event_slug}"
        logger.info(f"[HLTV][STATS] fetch_start match_id={match_id} url={url}")

        html = await self._client.fetch(url)
        if not html:
            logger.warning(f"[HLTV][STATS] fetch_empty match_id={match_id} url={url}")
            return None

        parsed = parse_match_stats(
            html,
            match_id=match_id,
            team1=team1,
            team2=team2,
            event_title=event_title,
        )

        if not parsed:
            logger.warning(f"[HLTV][STATS] parse_failed match_id={match_id} url={url}")
            return None

        logger.info(
            f"[HLTV][STATS] parse_ok match_id={match_id} maps={len(parsed.maps)} players={len(parsed.players)}"
        )
        return parsed

    async def get_latest_result_with_stats(
        self, event_id: str, event_title: str = ""
    ) -> Optional[MatchStats]:
        """获取最近一场比赛的详细数据"""
        results = await self.get_event_results(event_id, max_results=1)
        if not results:
            logger.warning(f"[HLTV][LATEST_STATS] no_results event={event_id}")
            return None

        result = results[0]
        logger.info(
            f"[HLTV][LATEST_STATS] picked_latest event={event_id} match_id={result.id} "
            f"teams={result.team1} vs {result.team2}"
        )

        return await self.get_match_stats(
            result.id,
            team1=result.team1,
            team2=result.team2,
            event_title=event_title,
        )


# 全局实例
hltv_data = HLTVDataSource()
