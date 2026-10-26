"""
HLTV match stats 页面解析（/matches/{id}/...）
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from plugins.hltv_sub.models import (
    HalfScore, MapStats, MatchStats, PlayerStats, VrsChange,
)
from plugins.hltv_sub.parsers.common import (
    absolute_url, extract_id_from_url, flag_of, format_date,
    is_grand_final_text, pick_logo, text_of,
)


def _parse_player_table(table: Tag, team: str) -> list[PlayerStats]:
    """按表头找列：有的比赛有 eADR、没有 Swing。K-D、ADR、Rating 缺了就是页面变了。"""
    headers = [
        cell.get_text(strip=True).upper()
        for cell in table.find("tr", class_="header-row").find_all(["th", "td"])
    ]

    def column(name: str, exclude: str = "") -> int:
        return next(
            (i for i, h in enumerate(headers) if name in h and not (exclude and exclude in h)),
            -1,
        )

    idx_kd, idx_adr, idx_rating = column("K-D"), column("ADR", "EADR"), column("RATING")
    idx_swing, idx_kast = column("SWING"), column("KAST", "EKAST")
    if -1 in (idx_kd, idx_adr, idx_rating):
        raise ValueError(f"HLTV 选手表缺少 K-D / ADR / Rating 列: {headers}")

    players: list[PlayerStats] = []
    rows = [
        r for r in table.find_all("tr")
        if not r.find("th") and "header-row" not in (r.get("class") or [])
    ]
    for row in rows[:5]:  # 每队最多5人
        cells = row.find_all("td")
        kills, deaths = cells[idx_kd].get_text(strip=True).split("-")
        player_link = row.find("a", href=re.compile(r"/player/\d+/"))
        country, country_name = flag_of(row.find("img", class_="flag"))
        players.append(
            PlayerStats(
                id=extract_id_from_url(player_link["href"]) if player_link else "",
                nickname=row.find("span", class_="player-nick").get_text(strip=True),
                team=team,
                kills=kills.strip(),
                deaths=deaths.strip(),
                adr=cells[idx_adr].get_text(strip=True),
                rating=cells[idx_rating].get_text(strip=True),
                swing=cells[idx_swing].get_text(strip=True) if idx_swing >= 0 else "-",
                kast=cells[idx_kast].get_text(strip=True) if idx_kast >= 0 else "-",
                country=country,
                country_name=country_name,
            )
        )
    return players


def _parse_team_tables(container: Tag | None, table_class) -> list[PlayerStats]:
    """前两张表依次是 team1、team2。"""
    if container is None:
        return []
    tables = container.find_all("table", class_=table_class)[:2]
    return [
        player
        for table, team in zip(tables, ("team1", "team2"))
        for player in _parse_player_table(table, team)
    ]


def _parse_halves(map_holder: Tag) -> list[HalfScore]:
    """半场比分形如 (8:4; 5:3)，每个数字的 class 是该队当时的阵营。"""
    holder = map_holder.find("div", class_="results-center-half-score")
    if not holder:
        return []
    digits = [
        span for span in holder.find_all("span")
        if span.get_text(strip=True).isdigit()
    ]
    halves: list[HalfScore] = []
    for first, second in zip(digits[::2], digits[1::2]):
        side = next(
            (c for c in (first.get("class") or []) if c in ("ct", "t")), ""
        )
        halves.append(
            HalfScore(
                score1=first.get_text(strip=True),
                score2=second.get_text(strip=True),
                team1_side=side,
            )
        )
    return halves


def _parse_team_box(soup: BeautifulSoup, index: int) -> tuple[str, str, str]:
    """返回 (队标, 旗帜代码, 国家/地区名)。"""
    box = soup.select_one(f".teamsBox .team{index}-gradient")
    if box is None:
        return "", "", ""
    country, country_name = flag_of(box.parent.find("img", class_=f"team{index}"))
    return pick_logo(box.find_all("img", class_="logo")), country, country_name


def _parse_vrs(soup: BeautifulSoup) -> tuple[VrsChange | None, VrsChange | None]:
    """赛后的 VRS result 块：左列 before，中列 result，两行依次是 team1、team2。

    赛前/赛中同一个容器是 forecast（current / if wins / if loses），不解析。
    """
    container = soup.select_one(".vrs-forecast-container")
    header = container.select_one(".vrs-forecast-middle .vrs-forecast-header") if container else None
    if header is None or header.get_text(strip=True).lower() != "result":
        return None, None

    def numbers(selector: str) -> list[tuple[int, int]]:
        values = []
        for wrapper in container.select(f"{selector} .vrs-forecast-numbers-wrapper"):
            points = re.search(r"[+-]?\d+", wrapper.select_one(".vrs-forecast-points").get_text())
            rank = re.search(r"\d+", wrapper.select_one(".vrs-forecast-ranking").get_text())
            # 未上榜的队伍没有数字，这时整块都不显示
            if not points or not rank:
                return []
            values.append((int(points.group()), int(rank.group())))
        return values

    before = numbers(".vrs-forecast-left-numbers")
    after = numbers(".vrs-forecast-middle")
    if len(before) != 2 or len(after) != 2:
        return None, None
    team1, team2 = (
        VrsChange(points_before=b[0], rank_before=b[1], points_delta=a[0], rank_after=a[1])
        for b, a in zip(before, after)
    )
    return team1, team2


def parse_match_stats(
    html: str,
    match_id: str,
    tz,
    team1: str = "",
    team2: str = "",
    event_title: str = "",
) -> MatchStats:
    """解析 match 页面。赛前、进行中、赛后结构不同，只有队名是必需的，其余缺了就空着。"""
    soup = BeautifulSoup(html, "lxml")

    team_names = soup.find_all("div", class_="teamName")
    if len(team_names) < 2:
        raise ValueError(f"HLTV 比赛 {match_id} 页面缺少队名，页面结构可能已变")
    team1 = team1 or team_names[0].get_text(strip=True)
    team2 = team2 or team_names[1].get_text(strip=True)

    # 系列赛比分：赛前和第一张图打完前没有，记 0
    score1, score2 = (
        score if score.isdigit() else "0"
        for score in (
            text_of(box.find(class_=re.compile(r"score|won|lost")))
            for box in soup.find_all("div", class_="team")[:2]
        )
    )

    team1_logo, team1_country, team1_country_name = _parse_team_box(soup, 1)
    team2_logo, team2_country, team2_country_name = _parse_team_box(soup, 2)
    team1_vrs, team2_vrs = _parse_vrs(soup)

    status = text_of(soup.find("div", class_="countdown")) or "完成"
    date_elem = soup.select_one(".timeAndEvent .date[data-unix]")
    match_date = format_date(date_elem["data-unix"], tz) if date_elem else ""
    event_title = event_title or text_of(soup.select_one(".timeAndEvent .event a"))

    # 第一个 veto-box 是赛制说明（"Best of 3 (LAN)" + "* Semi-final"），其后才是 BP。
    vetos: list[str] = []
    format_line = ""
    stage = ""
    is_grand_final = False
    for box in soup.find_all("div", class_="veto-box"):
        lines = [
            line.strip() for line in box.get_text("\n").split("\n") if line.strip()
        ]
        if box.find(class_="preformatted-text"):
            for line in lines:
                if line.startswith("*"):
                    stage = stage or line.lstrip("* ").strip()
                    # 决赛前后可能还有优势图等备注行，逐条认。
                    is_grand_final = is_grand_final or is_grand_final_text(line)
                else:
                    format_line = format_line or line
            continue
        vetos.extend(line for line in lines if not line.startswith("*"))

    maps = [
        map_info for map_info in map(_parse_map, soup.find_all("div", class_="mapholder"))
        if map_info is not None
    ]
    # 单图数据在 {mapstatsid}-content 里，没打完的图没有
    map_stats_details = {}
    for map_info in maps:
        if map_info.stats_id and (players := _parse_team_tables(
            soup.find("div", id=f"{map_info.stats_id}-content"),
            re.compile(r"stats-table|totalstats"),
        )):
            map_stats_details[map_info.map_name] = players

    return MatchStats(
        match_id=match_id,
        team1=team1,
        team2=team2,
        score1=score1,
        score2=score2,
        status=status,
        maps=maps,
        players=_parse_team_tables(soup.find("div", id="all-content"), "totalstats"),
        map_stats_details=map_stats_details,
        vetos=vetos,
        event=event_title,
        team1_logo=team1_logo,
        team2_logo=team2_logo,
        team1_country=team1_country,
        team1_country_name=team1_country_name,
        team2_country=team2_country,
        team2_country_name=team2_country_name,
        format_line=format_line,
        stage=stage,
        is_grand_final=is_grand_final,
        date=match_date,
        team1_vrs=team1_vrs,
        team2_vrs=team2_vrs,
    )


def _parse_map(map_holder: Tag) -> MapStats | None:
    """未定的图（TBA）返回 None；没打的图比分是 "-"。"""
    map_name = text_of(map_holder.find("div", class_="mapname"))
    if not map_name or map_name == "TBA":
        return None
    stats_link = map_holder.find("a", href=re.compile(r"mapstatsid/\d+"))
    left = map_holder.find(["div", "span"], class_="results-left")
    right = map_holder.find(["div", "span"], class_="results-right")

    def score(side: Tag | None) -> str:
        text = text_of(side.find("div", class_="results-team-score")) if side else ""
        return re.sub(r"[^\d-]", "", text) or "-"

    # 选图方那一侧带 pick class；两侧都没有就是 decider。
    pick_by = ""
    if left and "pick" in (left.get("class") or []):
        pick_by = "team1"
    elif right and "pick" in (right.get("class") or []):
        pick_by = "team2"
    minimap = map_holder.find("img", class_="minimap")
    return MapStats(
        map_name=map_name,
        pick_by=pick_by,
        score_team1=score(left),
        score_team2=score(right),
        stats_id=(
            re.search(r"mapstatsid/(\d+)", stats_link["href"]).group(1) if stats_link else ""
        ),
        halves=_parse_halves(map_holder),
        image=absolute_url(minimap.get("src", "")) if minimap else "",
    )
