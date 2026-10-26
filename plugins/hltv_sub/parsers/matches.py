"""赛事比赛页：一次读取比赛节点，同时保留不过滤 TBD 的时间提示。"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from nonebot import logger

from plugins.hltv_sub.models import (
    EventMatchesMeta, EventMatchesPage, MatchInfo, MatchTimeHint,
)
from plugins.hltv_sub.parsers.common import (
    is_grand_final_text, parse_start_time, pick_logo, text_of,
)

THIRD_PLACE_MARKERS = (
    "3rd place decider", "third place decider", "3rd place match", "third place match",
)


def _extract_maps_format(wrapper) -> str:
    meta = " ".join(
        elem.get_text(" ", strip=True)
        for elem in wrapper.find_all("div", class_="match-meta")
    )
    bo_match = re.search(r"\bbo(\d)\b", meta, re.IGNORECASE)
    return bo_match.group(1) if bo_match else ""


def _parse_matches(
    wrappers, tz, *, include_partial_tbd: bool = False,
) -> tuple[list[MatchInfo], list[MatchTimeHint]]:
    """默认只要两队都已确定的比赛；include_partial_tbd 时再加上一方是 winner 占位的。"""
    matches: list[MatchInfo] = []
    hints: list[MatchTimeHint] = []

    for wrapper in wrappers:
        match_id = wrapper.get("data-match-id", "")
        if not match_id:
            logger.warning(f"[HLTV] 跳过没有比赛 ID 的 match-wrapper: {text_of(wrapper)[:80]!r}")
            continue
        team1_id = wrapper.get("team1", "")
        team2_id = wrapper.get("team2", "")
        decided = bool(team1_id and team2_id)
        is_live = wrapper.get("live", "false") == "true"
        # 待定的比赛没有开赛时间
        time_elem = wrapper.find("div", class_="match-time")
        start_time = parse_start_time(time_elem.get("data-unix", "") if time_elem else "", tz)
        hints.append(MatchTimeHint(
            match_id=match_id, start_time=start_time, is_live=is_live, is_tbd=not decided,
        ))

        if not decided and not (include_partial_tbd and (team1_id or team2_id)):
            continue
        team1, team2 = _team_names(wrapper)
        if not decided and not _is_winner_placeholder(team2 if team1_id else team1):
            continue
        if not team1 or not team2:
            logger.warning(f"[HLTV] 跳过缺队名的比赛 {match_id}: {team1!r} vs {team2!r}")
            continue

        stars = wrapper.get("data-stars", "")
        no_info = wrapper.select_one("a.match-no-info")
        no_info_text = (
            " ".join(no_info.get_text(" ", strip=True).lower().split()) if no_info else ""
        )
        matches.append(
            MatchInfo(
                id=match_id,
                start_time=start_time,
                team1=team1,
                team2=team2,
                team1_id=team1_id,
                team2_id=team2_id,
                team1_logo=_team_logo(wrapper, 0),
                team2_logo=_team_logo(wrapper, 1),
                maps=_extract_maps_format(wrapper),
                rating=int(stars) if stars.isdigit() else 0,
                is_live=is_live,
                is_grand_final=_is_grand_final_match(wrapper),
                is_third_place=any(marker in no_info_text for marker in THIRD_PLACE_MARKERS),
            )
        )

    return matches, hints


def _analyze_matches_meta(
    event_id: str, final_url: str, soup: BeautifulSoup, wrapper_count: int
) -> EventMatchesMeta:
    meta = EventMatchesMeta(final_url=final_url)
    if final_url and f"/events/{event_id}/matches" not in final_url:
        meta.is_unavailable = True
        meta.unavailable_reason = "unexpected_final_url"
        return meta

    # 真实抓取证据：finished 赛事会返回通用 matches 页（标题固定 + 无 wrapper + no matches yet）
    title = text_of(soup.title).lower()
    text = soup.get_text(" ", strip=True).lower()
    if (
        "counter-strike matches & livescore" in title
        and wrapper_count == 0
        and "no matches" in text
    ):
        meta.is_unavailable = True
        meta.unavailable_reason = "generic_matches_page_no_event_matches"
    return meta


def parse_event_matches_page(
    html: str, final_url: str, event_id: str, tz,
    *, include_partial_tbd: bool = False,
) -> EventMatchesPage:
    soup = BeautifulSoup(html, "lxml")
    wrappers = soup.find_all("div", class_="match-wrapper")
    meta = _analyze_matches_meta(event_id, final_url, soup, len(wrappers))
    # 页面不可用时停止解析，避免从通用 matches 页误抓其他赛事。
    if meta.is_unavailable:
        logger.debug(
            f"[HLTV] 赛事 matches 页面不可用: event={event_id}, "
            f"final_url={meta.final_url}, reason={meta.unavailable_reason}"
        )
        return EventMatchesPage([], [], meta)

    matches, hints = _parse_matches(wrappers, tz, include_partial_tbd=include_partial_tbd)
    logger.debug(
        f"[HLTV] 获取到 {len(matches)} 场比赛 (filtered) / {len(hints)} 条时间提示 (raw), "
        f"event={event_id}, wrappers={len(wrappers)}"
    )
    return EventMatchesPage(matches, hints, meta)


def _is_winner_placeholder(name: str) -> bool:
    return "winner" in name.lower()


def _is_grand_final_match(wrapper) -> bool:
    stage_elem = wrapper.select_one("div.match-stage")
    if stage_elem and (
        "match-grand-final" in stage_elem.get("class", [])
        or is_grand_final_text(stage_elem.get_text(" ", strip=True))
    ):
        return True
    no_info = wrapper.select_one("a.match-no-info")
    return bool(no_info) and is_grand_final_text(no_info.get_text(" ", strip=True))


def _team_logo(wrapper, index: int) -> str:
    # 直播中的卡片 match-team 没有 team1/team2 class，只能按 .match-teams 下的顺序取；
    # 同卡片里比分那两个 match-team 挂在 .match-team-livescore 下，不会混进来。
    blocks = wrapper.select(".match-teams > div.match-team")
    if index >= len(blocks):
        return ""
    return pick_logo(blocks[index].find_all("img", class_="match-team-logo"))


def _team_names(wrapper) -> tuple[str, str]:
    """有 team1/team2 class 按 class 认，没有（直播卡片）按顺序认。"""
    team1 = team2 = ""
    for index, block in enumerate(wrapper.find_all("div", class_="match-team")):
        name = text_of(
            block.find("div", class_="match-teamname") or block.find("div", class_="team")
        )
        classes = block.get("class", [])
        if "team1" in classes:
            team1 = name
        elif "team2" in classes:
            team2 = name
        elif index == 0:
            team1 = name
        elif index == 1:
            team2 = name
    return team1, team2
