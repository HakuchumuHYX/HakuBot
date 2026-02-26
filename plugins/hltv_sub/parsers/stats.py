"""
HLTV match stats 页面解析（/matches/{id}/...）
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, Tag
from nonebot.log import logger

from ..models import MapStats, MatchStats, PlayerStats


def _extract_id_from_url(url: str) -> str:
    m = re.search(r"/(\d+)/", url or "")
    return m.group(1) if m else ""


def _parse_player_table(table: Tag, team_idx: int) -> List[PlayerStats]:
    """解析选手数据表格，动态查找列（复刻原逻辑）"""
    players: list[PlayerStats] = []

    try:
        # 1. 解析表头，确定列索引
        headers: list[str] = []
        header_row = table.find("tr", class_="header-row")
        if not header_row and table.find("thead"):
            header_row = table.find("thead").find("tr")  # type: ignore[union-attr]
        if not header_row:
            header_row = table.find("tr")

        if not header_row:
            return []

        cols = header_row.find_all(["th", "td"])
        headers = [c.get_text(strip=True).upper() for c in cols]

        idx_kd = -1
        idx_adr = -1
        idx_rating = -1
        idx_swing = -1
        idx_kast = -1

        for i, h in enumerate(headers):
            if "K-D" in h:
                idx_kd = i
            elif "ADR" in h and "EADR" not in h:  # 避免匹配到 eADR
                idx_adr = i
            elif "RATING" in h:
                idx_rating = i
            elif "SWING" in h:
                idx_swing = i
            elif "KAST" in h and "EKAST" not in h:
                idx_kast = i

        # 回退默认索引
        if idx_kd == -1 and len(headers) > 1:
            idx_kd = 1
        if idx_adr == -1 and len(headers) > 4:
            idx_adr = 4
        if idx_rating == -1:
            idx_rating = len(headers) - 1

        # 2. 解析数据行
        rows = table.find_all("tr")
        data_rows = [
            r
            for r in rows
            if not r.find("th") and "header-row" not in (r.get("class") or [])
        ]

        for row in data_rows[:5]:  # 每队最多5人
            cells = row.find_all("td")
            if not cells:
                continue

            player_link = row.find("a", href=re.compile(r"/player/\d+/"))
            player_id = (
                _extract_id_from_url(player_link.get("href", "")) if player_link else ""
            )

            nickname = ""
            nick_elem = row.find("span", class_="player-nick")
            if nick_elem:
                nickname = nick_elem.get_text(strip=True)

            if not nickname:
                mobile_name = row.find("div", class_="smartphone-only")
                if mobile_name:
                    nickname = mobile_name.get_text(strip=True)

            if not nickname and player_link:
                nickname = player_link.get_text(strip=True)

            if not nickname and len(cells) > 0:
                nickname = cells[0].get_text(strip=True)

            kd = "0-0"
            if idx_kd != -1 and idx_kd < len(cells):
                kd = cells[idx_kd].get_text(strip=True)

            adr = "0"
            if idx_adr != -1 and idx_adr < len(cells):
                adr = cells[idx_adr].get_text(strip=True)

            rating = "0"
            if idx_rating != -1 and idx_rating < len(cells):
                rating = cells[idx_rating].get_text(strip=True)

            swing = "-"
            if idx_swing != -1 and idx_swing < len(cells):
                swing = cells[idx_swing].get_text(strip=True)

            kast = "-"
            if idx_kast != -1 and idx_kast < len(cells):
                kast = cells[idx_kast].get_text(strip=True)

            kd_parts = kd.replace("/", "-").split("-")
            kills = kd_parts[0].strip() if len(kd_parts) > 0 else "0"
            deaths = kd_parts[1].strip() if len(kd_parts) > 1 else "0"

            if not kills.replace("-", "").isdigit():
                kills = "0"
            if not deaths.replace("-", "").isdigit():
                deaths = "0"

            players.append(
                PlayerStats(
                    id=player_id,
                    nickname=nickname,
                    team="team1" if team_idx == 0 else "team2",
                    kills=kills,
                    deaths=deaths,
                    adr=adr,
                    rating=rating,
                    swing=swing,
                    kast=kast,
                )
            )

    except Exception as e:
        logger.debug(f"[HLTV] 解析表格失败: {e}")

    return players


def parse_match_stats(
    html: str,
    match_id: str,
    team1: str = "",
    team2: str = "",
    event_title: str = "",
) -> Optional[MatchStats]:
    """解析 match 页面，抽取比分/地图/选手数据等（复刻原逻辑）"""
    soup = BeautifulSoup(html, "lxml")

    try:
        # 获取队伍名
        team_names = soup.find_all("div", class_="teamName")
        if len(team_names) >= 2:
            team1 = team1 or team_names[0].get_text(strip=True)
            team2 = team2 or team_names[1].get_text(strip=True)

        # 获取比分
        score1 = "0"
        score2 = "0"
        team_boxes = soup.find_all("div", class_="team")
        for i, box in enumerate(team_boxes[:2]):
            score_elem = box.find(class_=re.compile(r"score|won|lost"))
            if score_elem:
                score_text = score_elem.get_text(strip=True)
                if score_text.isdigit():
                    if i == 0:
                        score1 = score_text
                    else:
                        score2 = score_text

        if score1 == "0" and score2 == "0":
            score_elems = soup.find_all(class_=re.compile(r"score$|won$|lost$"))
            for elem in score_elems:
                text = elem.get_text(strip=True)
                if text.isdigit():
                    if score1 == "0":
                        score1 = text
                    elif score2 == "0":
                        score2 = text
                        break

        # 获取状态
        status = "完成"
        countdown = soup.find("div", class_="countdown")
        if countdown:
            status = countdown.get_text(strip=True)

        # 解析 Veto/BP
        vetos: list[str] = []
        veto_boxes = soup.find_all("div", class_="veto-box")
        for box in veto_boxes:
            lines = [line.strip() for line in box.get_text("\n").split("\n") if line.strip()]
            filtered_lines = [l for l in lines if not l.startswith("*")]
            if filtered_lines:
                vetos.extend(filtered_lines)

        # 解析地图列表和 stats id
        maps: list[MapStats] = []
        map_holders = soup.find_all("div", class_="mapholder")

        for map_holder in map_holders:
            try:
                map_name_elem = map_holder.find("div", class_="mapname")
                map_name = map_name_elem.get_text(strip=True) if map_name_elem else ""

                if not map_name or map_name == "TBA":
                    continue

                stats_id = ""
                stats_link = map_holder.find("a", href=re.compile(r"mapstatsid/(\d+)"))
                if stats_link:
                    m = re.search(r"mapstatsid/(\d+)", stats_link.get("href", "") or "")
                    if m:
                        stats_id = m.group(1)

                if not stats_id:
                    html_str = str(map_holder)
                    m = re.search(r"mapstatsid/(\d+)", html_str)
                    if m:
                        stats_id = m.group(1)

                map_score1 = "-"
                map_score2 = "-"

                results_left = map_holder.find(["div", "span"], class_="results-left")
                results_right = map_holder.find(["div", "span"], class_="results-right")

                if results_left:
                    score_elem = results_left.find("div", class_="results-team-score")
                    if score_elem:
                        map_score1 = score_elem.get_text(strip=True)

                if results_right:
                    score_elem = results_right.find("div", class_="results-team-score")
                    if score_elem:
                        map_score2 = score_elem.get_text(strip=True)

                if map_score1 == "-" and map_score2 == "-":
                    score_elems = map_holder.find_all(class_="results-team-score")
                    if len(score_elems) >= 2:
                        map_score1 = score_elems[0].get_text(strip=True)
                        map_score2 = score_elems[1].get_text(strip=True)

                map_score1 = re.sub(r"[^\d-]", "", map_score1) or "-"
                map_score2 = re.sub(r"[^\d-]", "", map_score2) or "-"

                pick_by = ""
                pick_elem = map_holder.find(class_=re.compile(r"pick|picked"))
                if pick_elem:
                    pick_text = pick_elem.get_text(strip=True)
                    if team1 and team1.lower() in pick_text.lower():
                        pick_by = team1
                    elif team2 and team2.lower() in pick_text.lower():
                        pick_by = team2

                maps.append(
                    MapStats(
                        map_name=map_name,
                        pick_by=pick_by,
                        score_team1=map_score1,
                        score_team2=map_score2,
                        stats_id=stats_id,
                    )
                )
            except Exception as e:
                logger.debug(f"[HLTV] 解析地图失败: {e}")
                continue

        # 解析总选手数据 (id="all-content")
        total_players: list[PlayerStats] = []
        all_content = soup.find("div", id="all-content")
        if all_content:
            stats_tables = all_content.find_all("table", class_="totalstats")
            for idx, table in enumerate(stats_tables[:2]):
                team_players = _parse_player_table(table, idx)
                total_players.extend(team_players)
        else:
            stats_tables = soup.find_all("table", class_="totalstats")
            if not stats_tables:
                all_tables = soup.find_all("table", class_=re.compile(r"stats|totalstats"))
                stats_tables = [t for t in all_tables if "hidden" not in (t.get("class") or [])]

            for idx, table in enumerate(stats_tables[:2]):
                team_players = _parse_player_table(table, idx)
                total_players.extend(team_players)

        # 解析单图选手数据
        map_stats_details: Dict[str, List[PlayerStats]] = {}
        for map_info in maps:
            if not map_info.stats_id:
                continue

            content_id = f"{map_info.stats_id}-content"
            content_div = soup.find("div", id=content_id)

            if content_div:
                map_players: list[PlayerStats] = []
                tables = content_div.find_all("table", class_=re.compile(r"stats-table|totalstats"))
                for idx, table in enumerate(tables[:2]):
                    team_players = _parse_player_table(table, idx)
                    map_players.extend(team_players)

                if map_players:
                    map_stats_details[map_info.map_name] = map_players

        return MatchStats(
            match_id=match_id,
            team1=team1,
            team2=team2,
            score1=score1,
            score2=score2,
            status=status,
            maps=maps,
            players=total_players,
            map_stats_details=map_stats_details,
            vetos=vetos,
            event=event_title,
        )

    except Exception as e:
        logger.error(f"[HLTV] 解析比赛数据失败: {e}")
        return None
