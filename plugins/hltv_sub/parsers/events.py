"""
HLTV events 页面解析
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from bs4 import BeautifulSoup
from nonebot import logger

from plugins.hltv_sub.models import EventInfo, EventMvp, MvpStat
from plugins.hltv_sub.parsers.common import (
    absolute_url, extract_id_from_url, flag_of, format_date, pick_logo,
)


def is_ongoing(start_date: str, end_date: str, tz) -> bool:
    """按页面给出的完整日期判断，不从展示字符串猜年份。"""
    try:
        today = datetime.now(tz).date()
        return date.fromisoformat(start_date) <= today <= date.fromisoformat(end_date)
    except ValueError:
        return False


def parse_big_events(html: str, tz) -> list[EventInfo]:
    """解析 /events 页面：ongoing + big-events"""
    events: list[EventInfo] = []
    soup = BeautifulSoup(html, "lxml")

    try:
        # 正在进行的赛事
        ongoing_section = soup.find("div", class_="ongoing-events-holder")
        if ongoing_section:
            for event_elem in ongoing_section.find_all("a", class_="ongoing-event"):
                try:
                    href = event_elem.get("href", "") or ""
                    event_id = extract_id_from_url(href)

                    # 获取赛事名称 - 排除 LAN/Online 标签
                    title = ""
                    name_container = event_elem.find("div", class_="event-name-small")
                    if name_container:
                        text_elem = name_container.find("div", class_="text-ellipsis")
                        if text_elem:
                            title = text_elem.get_text(strip=True)
                        else:
                            first_div = name_container.find("div")
                            if first_div and "lan-marker" not in first_div.get(
                                "class", []
                            ):
                                title = first_div.get_text(strip=True)

                    if not title:
                        name_elem = event_elem.find("span", class_="event-name")
                        title = name_elem.get_text(strip=True) if name_elem else ""

                    date_elems = event_elem.find_all("span", {"data-time-format": True})
                    start_date = ""
                    end_date = ""
                    if len(date_elems) >= 2:
                        start_date = format_date(
                            date_elems[0].get("data-unix", "") or "", tz
                        )
                        end_date = format_date(
                            date_elems[1].get("data-unix", "") or "", tz
                        )

                    if event_id and title:
                        events.append(
                            EventInfo(
                                id=event_id,
                                title=title,
                                start_date=start_date,
                                end_date=end_date,
                                logo=pick_logo(
                                    event_elem.find_all("img", class_="logo", limit=2)
                                ),
                                is_ongoing=True,
                            )
                        )
                except Exception as e:
                    logger.warning(f"[HLTV] 解析正在进行赛事失败: {e}")
                    continue

        # 即将举行的大型赛事
        big_events_sections = soup.find_all("div", class_="big-events")[:2]
        for big_events_section in big_events_sections:
            for event_elem in big_events_section.find_all("a", class_="big-event"):
                try:
                    href = event_elem.get("href", "") or ""
                    event_id = extract_id_from_url(href)

                    name_elem = event_elem.find("div", class_="big-event-name")
                    title = name_elem.get_text(strip=True) if name_elem else ""

                    date_elems = event_elem.find_all("span", {"data-time-format": True})
                    start_date = ""
                    end_date = ""
                    if len(date_elems) >= 2:
                        start_date = format_date(
                            date_elems[0].get("data-unix", "") or "", tz
                        )
                        end_date = format_date(
                            date_elems[1].get("data-unix", "") or "", tz
                        )

                    location_elem = event_elem.find("span", class_="big-event-location")
                    if event_id and title:
                        events.append(
                            EventInfo(
                                id=event_id,
                                title=title,
                                start_date=start_date,
                                end_date=end_date,
                                location=(
                                    location_elem.get_text(strip=True)
                                    if location_elem else ""
                                ),
                                country=flag_of(event_elem.find("img", class_="flag"))[0],
                                is_ongoing=False,
                            )
                        )
                except Exception as e:
                    logger.warning(f"[HLTV] 解析即将举行赛事失败: {e}")
                    continue

        # 通用回退
        if not events:
            event_holders = soup.find_all("a", href=re.compile(r"/events/\d+/"))
            seen_ids = set()
            for elem in event_holders[:30]:
                try:
                    href = elem.get("href", "") or ""
                    event_id = extract_id_from_url(href)
                    if event_id and event_id not in seen_ids:
                        seen_ids.add(event_id)

                        name_elem = elem.find(class_=re.compile(r"event.*name", re.I))
                        if not name_elem:
                            name_elem = elem.find("div")
                        title = (
                            name_elem.get_text(strip=True)
                            if name_elem
                            else f"Event {event_id}"
                        )

                        events.append(
                            EventInfo(
                                id=event_id,
                                title=title,
                                start_date="",
                                end_date="",
                                is_ongoing=False,
                            )
                        )
                except Exception:
                    continue

        logger.debug(f"[HLTV] 获取到 {len(events)} 个赛事")
    except Exception as e:
        logger.error(f"[HLTV] 解析赛事列表失败: {e}")

    return events


def parse_event_info(
    html: str, event_id: str, event_title: str, tz
) -> EventInfo | None:
    """解析 /events/{id}/{slug} 页面"""
    soup = BeautifulSoup(html, "lxml")
    try:
        title_elem = soup.find("h1", class_="event-hub-title")
        title = title_elem.get_text(strip=True) if title_elem else event_title

        date_elem = soup.find("td", class_="eventdate")
        start_date = ""
        end_date = ""
        if date_elem:
            spans = date_elem.find_all("span", {"data-unix": True})
            if len(spans) >= 2:
                start_date = format_date(spans[0].get("data-unix", "") or "", tz)
                end_date = format_date(spans[1].get("data-unix", "") or "", tz)

        prize_elem = soup.find("td", class_="prizepool")
        prize = prize_elem.get_text(strip=True) if prize_elem else ""

        teams_elem = soup.find("td", class_="teamsNumber")
        teams = teams_elem.get_text(strip=True) if teams_elem else ""

        location_elem = soup.find("td", class_="location")
        location = location_elem.get_text(strip=True) if location_elem else ""

        return EventInfo(
            id=event_id,
            title=title,
            start_date=start_date,
            end_date=end_date,
            prize=prize,
            teams=teams,
            location=location,
            is_ongoing=is_ongoing(start_date, end_date, tz),
            mvp=parse_event_mvp(soup),
        )
    except Exception as e:
        logger.error(f"[HLTV] 解析赛事信息失败: {e}")
        return None


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
