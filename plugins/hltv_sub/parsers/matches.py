"""赛事比赛页：一次读取比赛节点，同时保留不过滤 TBD 的时间提示。"""

from __future__ import annotations

import re
from typing import Tuple

from bs4 import BeautifulSoup
from nonebot.log import logger

from plugins.hltv_sub.models import (
    EventMatchesMeta, EventMatchesPage, MatchInfo, MatchTimeHint,
)
from plugins.hltv_sub.parsers.common import extract_id_from_url, parse_start_time


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


def _parse_matches(
    soup: BeautifulSoup,
    tz,
    *,
    include_partial_tbd: bool = False,
) -> tuple[list[MatchInfo], list[MatchTimeHint]]:
    """解析 matches 页面的比赛列表（默认过滤 TBD）"""
    matches: list[MatchInfo] = []
    hints: list[MatchTimeHint] = []

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

            time_elem = wrapper.find("div", class_="match-time")
            start_time = parse_start_time(
                time_elem.get("data-unix", "") if time_elem else "", tz
            )
            hints.append(MatchTimeHint(
                match_id=match_id, start_time=start_time,
                is_live=is_live, is_tbd=not (team1_id and team2_id),
            ))

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

            maps_format = _extract_maps_format(wrapper)

            rating = 0
            try:
                rating = int(stars) if stars else 0
            except ValueError:
                rating = 0

            matches.append(
                MatchInfo(
                    id=match_id,
                    start_time=start_time,
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
                match_id = extract_id_from_url(href)

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
                        start_time=None,
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

    return matches, hints


def _analyze_matches_meta(
    event_id: str, final_url: str, soup: BeautifulSoup
) -> EventMatchesMeta:
    title = soup.title.get_text(strip=True) if soup.title else ""

    wrappers = soup.find_all("div", class_="match-wrapper")
    text = soup.get_text(" ", strip=True).lower()

    meta = EventMatchesMeta(
        final_url=final_url,
        page_title=title,
        match_wrapper_count=len(wrappers),
    )

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


def parse_event_matches_page(
    html: str, final_url: str, event_id: str, tz,
    *, include_partial_tbd: bool = False,
) -> EventMatchesPage:
    soup = BeautifulSoup(html, "lxml")
    meta = _analyze_matches_meta(event_id, final_url, soup)
    # 页面不可用时停止解析，避免从通用 matches 页误抓其他赛事。
    if meta.is_unavailable:
        logger.warning(
            f"[HLTV] 赛事 matches 页面不可用: event={event_id}, "
            f"final_url={meta.final_url}, title={meta.page_title}, "
            f"reason={meta.unavailable_reason}"
        )
        return EventMatchesPage([], [], meta)

    matches, hints = _parse_matches(soup, tz, include_partial_tbd=include_partial_tbd)
    logger.debug(
        f"[HLTV] 获取到 {len(matches)} 场比赛 (filtered) / {len(hints)} 条时间提示 (raw), "
        f"event={event_id}, wrappers={meta.match_wrapper_count}"
    )
    return EventMatchesPage(matches, hints, meta)


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
    return _contains_third_place_marker(
        no_info.get_text(" ", strip=True) if no_info else ""
    )


def _extract_team_names_from_wrapper(wrapper) -> Tuple[str, str]:
    team1 = ""
    team2 = ""

    team_blocks = wrapper.find_all("div", class_="match-team")
    for index, block in enumerate(team_blocks):
        name_elem = block.find("div", class_="match-teamname") or block.find(
            "div", class_="team"
        )
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
