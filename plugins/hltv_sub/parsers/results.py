"""
HLTV results 页面解析
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from nonebot import logger

from plugins.hltv_sub.models import ResultInfo
from plugins.hltv_sub.parsers.common import (
    extract_id_from_url, format_date, pick_logo, text_of,
)


def parse_event_results(html: str, tz, max_results: int = 20) -> list[ResultInfo]:
    """解析 /results?event=xxx 页面；单条异常跳过，一条都解析不出来才算页面失败。"""
    soup = BeautifulSoup(html, "lxml")
    containers = soup.find_all("div", class_="result-con")[:max_results]
    results = [r for r in (_parse_result(c, tz) for c in containers) if r is not None]
    if containers and not results:
        raise ValueError("HLTV 结果页没有可解析的结果，页面结构可能已变")
    logger.debug(f"[HLTV] 获取到 {len(results)} 个比赛结果")
    return results


def _parse_result(container, tz) -> ResultInfo | None:
    link = container.find("a", href=re.compile(r"/matches/\d+/"))
    if link is None:
        logger.warning(f"[HLTV] 跳过没有比赛链接的结果节点: {text_of(container)[:80]!r}")
        return None
    match_id = extract_id_from_url(link["href"])

    cells = link.find_all("td", class_="team-cell")[:2]
    teams = [cell.find("div", class_="team") for cell in cells]
    team1, team2 = (text_of(team) for team in teams) if len(teams) == 2 else ("", "")
    score_cell = link.find("td", class_="result-score")
    won = text_of(score_cell.find("span", class_="score-won")) if score_cell else ""
    lost = text_of(score_cell.find("span", class_="score-lost")) if score_cell else ""
    team1_won = bool(team1) and "team-won" in teams[0].get("class", [])
    score1, score2 = (won, lost) if team1_won else (lost, won)
    if not team1 or not team2 or not score1.isdigit() or not score2.isdigit():
        logger.warning(
            f"[HLTV] 跳过不完整的结果 {match_id}: {team1!r} {score1!r}-{score2!r} {team2!r}"
        )
        return None

    return ResultInfo(
        id=match_id,
        date=format_date(container.get("data-zonedgrouping-entry-unix", ""), tz),
        team1=team1,
        team2=team2,
        score1=score1,
        score2=score2,
        team1_logo=pick_logo(cells[0].find_all("img", class_="team-logo")),
        team2_logo=pick_logo(cells[1].find_all("img", class_="team-logo")),
        # bo1 时这里是地图名，bo3/bo5 时是赛制。
        format=text_of(link.find("div", class_="map-text")),
    )
