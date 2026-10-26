"""
HLTV events 页面解析
"""

from __future__ import annotations

import json
from datetime import date, datetime

from bs4 import BeautifulSoup
from nonebot import logger

from plugins.hltv_sub.models import EventInfo, EventMvp, MvpStat
from plugins.hltv_sub.parsers.common import (
    absolute_url, extract_id_from_url, flag_of, format_date, pick_logo, text_of,
)


def is_ongoing(start_date: str, end_date: str, tz) -> bool:
    """按页面给出的完整日期判断，不从展示字符串猜年份。"""
    today = datetime.now(tz).date()
    return date.fromisoformat(start_date) <= today <= date.fromisoformat(end_date)


def parse_big_events(html: str, tz) -> list[EventInfo]:
    """解析 /events 页面：正在进行的赛事 + 前两组 big-events。"""
    soup = BeautifulSoup(html, "lxml")
    ongoing_section = soup.find("div", class_="ongoing-events-holder")
    ongoing = ongoing_section.find_all("a", class_="ongoing-event") if ongoing_section else []
    upcoming = [
        elem
        for section in soup.find_all("div", class_="big-events")[:2]
        for elem in section.find_all("a", class_="big-event")
    ]
    if not ongoing and not upcoming:
        raise ValueError("HLTV 赛事页没有赛事卡片，页面结构可能已变")

    events: list[EventInfo] = []
    for elem in ongoing:
        if event := _parse_event_card(elem, ".event-name-small .text-ellipsis", tz):
            event.logo = pick_logo(elem.find_all("img", class_="logo", limit=2))
            event.is_ongoing = True
            events.append(event)
    for elem in upcoming:
        if event := _parse_event_card(elem, "div.big-event-name", tz):
            event.location = text_of(elem.find("span", class_="big-event-location"))
            event.country = flag_of(elem.find("img", class_="flag"))[0]
            events.append(event)
    if not events:
        raise ValueError("HLTV 赛事页没有可解析的赛事，页面结构可能已变")
    logger.debug(f"[HLTV] 获取到 {len(events)} 个赛事")
    return events


def _parse_event_card(elem, title_selector: str, tz) -> EventInfo | None:
    """两种卡片共有的 ID、标题、起止日期；缺了就跳过这张卡片。"""
    event_id = extract_id_from_url(elem.get("href", ""))
    title = text_of(elem.select_one(title_selector))
    dates = [
        format_date(span.get("data-unix", ""), tz)
        for span in elem.find_all("span", {"data-time-format": True})[:2]
    ]
    if not event_id or not title or len(dates) < 2 or not all(dates):
        logger.warning(f"[HLTV] 跳过不完整的赛事卡片: id={event_id!r} title={title!r}")
        return None
    return EventInfo(id=event_id, title=title, start_date=dates[0], end_date=dates[1])


def parse_event_info(html: str, event_id: str, tz) -> EventInfo:
    """解析 /events/{id}/{slug} 页面"""
    soup = BeautifulSoup(html, "lxml")
    title = text_of(soup.find("h1", class_="event-hub-title"))
    date_elem = soup.find("td", class_="eventdate")
    spans = date_elem.find_all("span", {"data-unix": True}) if date_elem else []
    dates = [format_date(span["data-unix"], tz) for span in spans[:2]]
    if not title or len(dates) < 2 or not all(dates):
        raise ValueError(f"HLTV 赛事 {event_id} 详情页缺少标题或日期，页面结构可能已变")
    return EventInfo(
        id=event_id,
        title=title,
        start_date=dates[0],
        end_date=dates[1],
        prize=text_of(soup.find("td", class_="prizepool")),
        teams=text_of(soup.find("td", class_="teamsNumber")),
        location=text_of(soup.find("td", class_="location")),
        is_ongoing=is_ongoing(dates[0], dates[1], tz),
        mvp=parse_event_mvp(soup),
    )


def parse_event_mvp(soup: BeautifulSoup) -> EventMvp | None:
    """赛后详情页的 MVP 区块；未评出时整块不存在。"""
    box = soup.select_one("div.standard-box.mvp")
    if box is None:
        return None
    # 名字形如 Mathieu <span class="bold">'ZywOo'</span> Herbaut
    name = box.select_one(".player-name a")
    nick = name.find("span")
    country, country_name = flag_of(box.select_one(".player-name img.flag"))
    # 各项数据只在柱状图配置里有；按页面给的顺序和名称读，不写死项目。
    chart = json.loads(box.select_one("[data-chartjs-config]")["data-chartjs-config"])
    return EventMvp(
        nickname=nick.get_text(strip=True).strip("'"),
        first_name="".join(nick.find_previous_siblings(string=True)).strip(),
        last_name="".join(nick.find_next_siblings(string=True)).strip(),
        country=country,
        country_name=country_name,
        photo=pick_logo(box.select(".mvp-picture img")),
        medal=absolute_url(box.select_one("img.mvp-coin")["src"]),
        stats=[
            MvpStat(item["category"], item["label"], item["value"])
            for item in chart["data"]["datasets"][0]["data"]
        ],
    )
