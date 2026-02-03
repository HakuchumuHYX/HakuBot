"""
HLTV matches 页面解析

- parse_event_matches：过滤 TBD（用于渲染/提醒）
- parse_match_time_hints：不过滤 TBD（用于 scheduler 自适应轮询）
"""

from __future__ import annotations

import re
from typing import Tuple

from bs4 import BeautifulSoup
from nonebot.log import logger

from ..models import MatchInfo, MatchTimeHint
from .common import format_date, format_time


def parse_match_time_hints(soup: BeautifulSoup, tz) -> list[MatchTimeHint]:
    """解析 matches 页面的时间提示（不过滤 TBD）"""
    hints: list[MatchTimeHint] = []
    try:
        match_wrappers = soup.find_all("div", class_="match-wrapper")
        for wrapper in match_wrappers:
            try:
                match_id = wrapper.get("data-match-id", "") or ""
                if not match_id:
                    continue

                team1_id = wrapper.get("team1", "") or ""
                team2_id = wrapper.get("team2", "") or ""
                is_tbd = (not team1_id) or (not team2_id)
                is_live = (wrapper.get("live", "false") or "false") == "true"

                match_time = ""
                match_date = ""
                time_elem = wrapper.find("div", class_="match-time")
                if time_elem:
                    unix_ts = time_elem.get("data-unix", "") or ""
                    if unix_ts:
                        match_time = format_time(unix_ts, tz)
                        match_date = format_date(unix_ts, tz)
                    else:
                        match_time = time_elem.get_text(strip=True)

                hints.append(
                    MatchTimeHint(
                        match_id=match_id,
                        date=match_date if not is_live else "LIVE",
                        time=match_time if not is_live else "LIVE",
                        is_live=is_live,
                        is_tbd=is_tbd,
                    )
                )
            except Exception:
                continue
    except Exception:
        return hints

    return hints


def parse_event_matches(soup: BeautifulSoup, tz) -> list[MatchInfo]:
    """解析 matches 页面的比赛列表（过滤 TBD）"""
    matches: list[MatchInfo] = []

    # 方法1: 使用 match-wrapper 结构（最精确）
    match_wrappers = soup.find_all("div", class_="match-wrapper")

    for wrapper in match_wrappers:
        try:
            match_id = wrapper.get("data-match-id", "")
            team1_id = wrapper.get("team1", "")
            team2_id = wrapper.get("team2", "")
            stars = wrapper.get("data-stars", "0")
            is_live = wrapper.get("live", "false") == "true"

            if not match_id:
                continue

            # 过滤 TBD（保持原行为）
            if not team1_id or not team2_id:
                continue

            team1 = ""
            team2 = ""

            team_elems = wrapper.find_all("div", class_="match-teamname")
            if len(team_elems) >= 2:
                team1 = team_elems[0].get_text(strip=True)
                team2 = team_elems[1].get_text(strip=True)

            if not team1 or not team2:
                link = wrapper.find("a", href=re.compile(r"/matches/\d+/"))
                if link:
                    href = link.get("href", "")
                    match_result = re.search(r"/([^/]+)-vs-([^/]+)-", href)
                    if match_result:
                        team1 = team1 or match_result.group(1).replace("-", " ").title()
                        team2 = team2 or match_result.group(2).replace("-", " ").title()

            if not team1 or not team2:
                continue

            match_time = ""
            match_date = ""
            time_elem = wrapper.find("div", class_="match-time")
            if time_elem:
                unix_ts = time_elem.get("data-unix", "")
                if unix_ts:
                    match_time = format_time(unix_ts, tz)
                    match_date = format_date(unix_ts, tz)
                else:
                    match_time = time_elem.get_text(strip=True)

            maps_format = ""
            meta_elem = wrapper.find("div", class_="match-meta")
            if meta_elem:
                meta_text = meta_elem.get_text(strip=True).lower()
                bo_match = re.search(r"bo(\d)", meta_text)
                if bo_match:
                    maps_format = bo_match.group(1)

            rating = 0
            try:
                rating = int(stars) if stars else 0
            except ValueError:
                rating = 0

            matches.append(
                MatchInfo(
                    id=match_id,
                    date=match_date if not is_live else "LIVE",
                    time=match_time if not is_live else "LIVE",
                    team1=team1,
                    team2=team2,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    maps=maps_format,
                    rating=rating,
                    is_live=is_live,
                )
            )

        except Exception as e:
            logger.debug(f"[HLTV] 解析单个 match-wrapper 失败: {e}")
            continue

    # 方法2: 如果没找到 match-wrapper，回退到链接解析（保持原逻辑）
    if not matches:
        logger.debug("[HLTV] 未找到 match-wrapper，尝试链接解析")
        match_links = soup.find_all("a", href=re.compile(r"/matches/\d+/"))
        seen_ids = set()

        for link in match_links:
            try:
                href = link.get("href", "")
                match_id = _extract_id_from_url(href)

                if not match_id or match_id in seen_ids:
                    continue
                seen_ids.add(match_id)

                match_result = re.search(r"/([^/]+)-vs-([^/]+)-", href)
                if not match_result:
                    continue

                team1 = match_result.group(1).replace("-", " ").title()
                team2 = match_result.group(2).replace("-", " ").title()

                # 过滤 TBD
                text = link.get_text(" ", strip=True)
                if "TBD" in text.upper():
                    continue

                is_live = "LIVE" in text.upper()

                matches.append(
                    MatchInfo(
                        id=match_id,
                        date="LIVE" if is_live else "",
                        time="LIVE" if is_live else "",
                        team1=team1,
                        team2=team2,
                        team1_id="",
                        team2_id="",
                        maps="",
                        rating=0,
                        is_live=is_live,
                    )
                )

            except Exception as e:
                logger.debug(f"[HLTV] 解析单个比赛链接失败: {e}")
                continue

    return matches


def parse_event_matches_with_hints(soup: BeautifulSoup, tz) -> tuple[list[MatchInfo], list[MatchTimeHint]]:
    """单次解析得到 filtered matches + raw hints"""
    matches = parse_event_matches(soup, tz)
    hints = parse_match_time_hints(soup, tz)
    return matches, hints


def _extract_id_from_url(url: str) -> str:
    match = re.search(r"/(\d+)/", url or "")
    return match.group(1) if match else ""
