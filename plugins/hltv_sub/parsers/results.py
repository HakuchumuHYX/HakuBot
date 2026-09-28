"""
HLTV results 页面解析
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from nonebot.log import logger

from plugins.hltv_sub.models import ResultInfo
from plugins.hltv_sub.parsers.common import extract_id_from_url


def parse_event_results(html: str, max_results: int = 20) -> list[ResultInfo]:
    """解析 /results?event=xxx 页面"""
    results: list[ResultInfo] = []
    soup = BeautifulSoup(html, "lxml")

    result_containers = soup.find_all("div", class_="result-con")

    for container in result_containers[:max_results]:
        link = container.find("a", href=re.compile(r"/matches/\d+/"))
        if not link:
            raise ValueError("HLTV 结果节点缺少比赛链接")

        href = link.get("href", "") or ""
        match_id = extract_id_from_url(href)
        if not match_id:
            raise ValueError("HLTV 结果节点缺少比赛 ID")

        team_cells = link.find_all("td", class_="team-cell")
        team1 = ""
        team2 = ""

        if len(team_cells) >= 2:
            team1_elem = team_cells[0].find("div", class_="team")
            team2_elem = team_cells[1].find("div", class_="team")
            team1 = team1_elem.get_text(strip=True) if team1_elem else ""
            team2 = team2_elem.get_text(strip=True) if team2_elem else ""

        if not team1 or not team2:
            m = re.search(r"/([^/]+)-vs-([^/]+)-", href)
            if m:
                team1 = team1 or m.group(1).replace("-", " ").title()
                team2 = team2 or m.group(2).replace("-", " ").title()

        score_cell = link.find("td", class_="result-score")
        score1 = ""
        score2 = ""

        if score_cell:
            score_won = score_cell.find("span", class_="score-won")
            score_lost = score_cell.find("span", class_="score-lost")

            team1_cell = team_cells[0] if len(team_cells) >= 2 else None
            team1_won = False
            if team1_cell:
                team1_div = team1_cell.find("div", class_="team")
                if team1_div and "team-won" in (team1_div.get("class") or []):
                    team1_won = True

            if team1_won:
                score1 = score_won.get_text(strip=True) if score_won else ""
                score2 = score_lost.get_text(strip=True) if score_lost else ""
            else:
                score1 = score_lost.get_text(strip=True) if score_lost else ""
                score2 = score_won.get_text(strip=True) if score_won else ""

        if not team1 or not team2 or not score1.isdigit() or not score2.isdigit():
            raise ValueError(f"HLTV 比赛 {match_id} 的结果不完整")
        results.append(
            ResultInfo(
                id=match_id,
                date="",
                team1=team1,
                team2=team2,
                score1=score1,
                score2=score2,
            )
        )

    logger.debug(f"[HLTV] 获取到 {len(results)} 个比赛结果")
    return results
