"""
HLTV matches 页面解析

- parse_event_matches：默认过滤 TBD，展示模式允许单边 TBD
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


def _extract_maps_format(wrapper) -> str:
    meta_texts = [
        elem.get_text(" ", strip=True)
        for elem in wrapper.find_all("div", class_="match-meta")
    ]
    search_space = " ".join(meta_texts)
    if not search_space:
        search_space = wrapper.get_text(" ", strip=True)

    bo_match = re.search(r"\bbo(\d)\b", search_space, re.IGNORECASE)
    if not bo_match:
        return ""
    return bo_match.group(1)


def parse_event_matches(
    soup: BeautifulSoup,
    tz,
    *,
    include_partial_tbd: bool = False,
) -> list[MatchInfo]:
    """解析 matches 页面的比赛列表（默认过滤 TBD）"""
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

            has_team1_id = bool(team1_id)
            has_team2_id = bool(team2_id)
            if include_partial_tbd:
                if not (has_team1_id or has_team2_id):
                    continue
            elif not (has_team1_id and has_team2_id):
                continue

            team1, team2 = _extract_team_names_from_wrapper(wrapper)

            if not team1 or not team2:
                if include_partial_tbd and not (has_team1_id and has_team2_id):
                    continue

                link = wrapper.find("a", href=re.compile(r"/matches/\d+/"))
                if link:
                    href = link.get("href", "")
                    match_result = re.search(r"/([^/]+)-vs-([^/]+)-", href)
                    if match_result:
                        team1 = team1 or match_result.group(1).replace("-", " ").title()
                        team2 = team2 or match_result.group(2).replace("-", " ").title()

            if not team1 or not team2:
                continue

            if include_partial_tbd and not (has_team1_id and has_team2_id):
                placeholder_name = team1 if not has_team1_id else team2
                if not _is_winner_placeholder(placeholder_name):
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

            maps_format = _extract_maps_format(wrapper)

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
                    is_grand_final=_is_grand_final_match(wrapper),
                    is_third_place=_is_third_place_match(wrapper),
                )
            )

        except Exception as e:
            logger.debug(f"[HLTV] 解析单个 match-wrapper 失败: {e}")
            continue

    # 方法2: 仅当页面结构中完全不存在 match-wrapper 时，才回退到链接解析
    # 注意：若 match-wrapper 存在但都因 TBD 被过滤，应该返回空，避免误抓页面中其他赛事链接
    if not matches and not match_wrappers:
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
                        is_grand_final="grand final" in text.lower(),
                        is_third_place=_contains_third_place_marker(text),
                    )
                )

            except Exception as e:
                logger.debug(f"[HLTV] 解析单个比赛链接失败: {e}")
                continue

    return matches


def parse_event_matches_with_hints(
    soup: BeautifulSoup,
    tz,
    *,
    include_partial_tbd: bool = False,
) -> tuple[list[MatchInfo], list[MatchTimeHint]]:
    """单次解析得到 filtered matches + raw hints"""
    matches = parse_event_matches(soup, tz, include_partial_tbd=include_partial_tbd)
    hints = parse_match_time_hints(soup, tz)
    return matches, hints


def _is_winner_placeholder(name: str) -> bool:
    normalized = " ".join((name or "").lower().split())
    return normalized not in {"", "tbd", "tba"} and "winner" in normalized


def _is_grand_final_match(wrapper) -> bool:
    stage_elem = wrapper.select_one("div.match-stage")
    if stage_elem:
        stage_text = stage_elem.get_text(" ", strip=True).lower()
        stage_classes = stage_elem.get("class", [])
        if "match-grand-final" in stage_classes or "grand final" in stage_text:
            return True

    no_info = wrapper.select_one("a.match-no-info")
    if no_info and "grand final" in no_info.get_text(" ", strip=True).lower():
        return True

    return False


def _contains_third_place_marker(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    return any(
        marker in normalized
        for marker in (
            "3rd place decider",
            "third place decider",
            "3rd place match",
            "third place match",
        )
    )


def _is_third_place_match(wrapper) -> bool:
    no_info = wrapper.select_one("a.match-no-info")
    return _contains_third_place_marker(no_info.get_text(" ", strip=True) if no_info else "")


def _extract_team_names_from_wrapper(wrapper) -> Tuple[str, str]:
    team1 = ""
    team2 = ""

    team_blocks = wrapper.find_all("div", class_="match-team")
    for index, block in enumerate(team_blocks):
        name_elem = block.find("div", class_="match-teamname") or block.find("div", class_="team")
        name = name_elem.get_text(strip=True) if name_elem else ""
        classes = block.get("class", [])
        if "team1" in classes:
            team1 = name
        elif "team2" in classes:
            team2 = name
        elif index == 0:
            team1 = name
        elif index == 1:
            team2 = name

    if team1 and team2:
        return team1, team2

    team_elems = wrapper.find_all("div", class_="match-teamname")
    if len(team_elems) >= 2:
        team1 = team1 or team_elems[0].get_text(strip=True)
        team2 = team2 or team_elems[1].get_text(strip=True)

    return team1, team2


def _extract_id_from_url(url: str) -> str:
    match = re.search(r"/(\d+)/", url or "")
    return match.group(1) if match else ""
