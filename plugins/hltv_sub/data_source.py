"""HLTV 数据源 - 使用 curl_cffi 绕过 Cloudflare"""

import re
import asyncio
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
import pytz
from bs4 import BeautifulSoup

from curl_cffi.requests import AsyncSession
from nonebot.log import logger

from .config import plugin_config


@dataclass
class EventInfo:
    """赛事信息"""
    id: str
    title: str
    start_date: str
    end_date: str
    prize: str = ""
    teams: str = ""
    location: str = ""
    is_ongoing: bool = False


@dataclass
class MatchInfo:
    """比赛信息"""
    id: str
    date: str
    time: str
    team1: str
    team2: str
    team1_id: str
    team2_id: str
    maps: str = ""
    rating: int = 0
    event: str = ""
    is_live: bool = False


@dataclass
class ResultInfo:
    """结果信息"""
    id: str
    date: str
    team1: str
    team2: str
    score1: str
    score2: str
    event: str = ""


@dataclass
class MapStats:
    """地图数据"""
    map_name: str
    pick_by: str  # team1, team2, or decider
    score_team1: str
    score_team2: str


@dataclass
class PlayerStats:
    """选手数据"""
    id: str
    nickname: str
    team: str  # team1 or team2
    kills: str
    deaths: str
    adr: str
    rating: str


@dataclass
class MatchStats:
    """比赛详细数据"""
    match_id: str
    team1: str
    team2: str
    score1: str
    score2: str
    status: str
    maps: list[MapStats]
    players: list[PlayerStats]
    vetos: list[str] = field(default_factory=list)
    event: str = ""


class HLTVDataSource:
    """HLTV 数据源 - 使用 curl_cffi 绕过 Cloudflare"""
    
    BASE_URL = "https://www.hltv.org"
    
    def __init__(self):
        self._session: Optional[AsyncSession] = None
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
    
    async def _get_session(self) -> AsyncSession:
        """获取会话"""
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome")
        return self._session
    
    async def close(self):
        """关闭会话"""
        if self._session is not None:
            await self._session.close()
            self._session = None
    
    def _get_proxy(self) -> Optional[str]:
        """获取代理"""
        if plugin_config.hltv_proxy_list:
            return plugin_config.hltv_proxy_list[0]
        return None
    
    async def _fetch(self, url: str, max_retries: int = 5) -> Optional[str]:
        """发送请求获取 HTML"""
        session = await self._get_session()
        proxy = self._get_proxy()
        
        for attempt in range(max_retries):
            try:
                # 添加随机延迟
                if attempt > 0:
                    delay = plugin_config.hltv_min_delay + (attempt * 2)
                    logger.info(f"[HLTV] 重试 {attempt + 1}/{max_retries}，延迟 {delay}s...")
                    await asyncio.sleep(delay)
                
                logger.info(f"[HLTV] 正在请求: {url}")
                
                response = await session.get(
                    url,
                    proxy=proxy,
                    timeout=plugin_config.hltv_timeout,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                    }
                )
                
                if response.status_code == 200:
                    logger.debug(f"[HLTV] 请求成功: {url}")
                    return response.text
                elif response.status_code == 403:
                    logger.warning(f"[HLTV] 403 Forbidden: {url}")
                    continue
                else:
                    logger.warning(f"[HLTV] HTTP {response.status_code}: {url}")
                    continue
                    
            except Exception as e:
                logger.error(f"[HLTV] 请求失败: {e}")
                continue
        
        logger.error(f"[HLTV] 请求失败，已达最大重试次数: {url}")
        return None
    
    async def get_big_events(self) -> list[EventInfo]:
        """获取 Big Events（正在进行 + 即将举行的赛事）"""
        html = await self._fetch(f"{self.BASE_URL}/events")
        
        if not html:
            return []
        
        events = []
        soup = BeautifulSoup(html, "lxml")
        
        try:
        # 正在进行的赛事
            ongoing_section = soup.find("div", class_="ongoing-events-holder")
            if ongoing_section:
                for event_elem in ongoing_section.find_all("a", class_="ongoing-event"):
                    try:
                        href = event_elem.get("href", "")
                        event_id = self._extract_id_from_url(href)
                        
                        # 获取赛事名称 - 需要排除 LAN/Online 标签
                        title = ""
                        name_container = event_elem.find("div", class_="event-name-small")
                        if name_container:
                            # 优先获取 text-ellipsis 子元素，避免获取 lan-marker
                            text_elem = name_container.find("div", class_="text-ellipsis")
                            if text_elem:
                                title = text_elem.get_text(strip=True)
                            else:
                                # 回退：获取第一个子 div 的文本
                                first_div = name_container.find("div")
                                if first_div and "lan-marker" not in first_div.get("class", []):
                                    title = first_div.get_text(strip=True)
                        
                        # 备用方式
                        if not title:
                            name_elem = event_elem.find("span", class_="event-name")
                            title = name_elem.get_text(strip=True) if name_elem else ""
                        
                        # 获取日期
                        date_elems = event_elem.find_all("span", {"data-time-format": True})
                        start_date = ""
                        end_date = ""
                        if len(date_elems) >= 2:
                            start_date = self._format_date(date_elems[0].get("data-unix", ""))
                            end_date = self._format_date(date_elems[1].get("data-unix", ""))
                        
                        if event_id and title:
                            events.append(EventInfo(
                                id=event_id,
                                title=title,
                                start_date=start_date,
                                end_date=end_date,
                                is_ongoing=True
                            ))
                    except Exception as e:
                        logger.warning(f"[HLTV] 解析正在进行赛事失败: {e}")
                        continue
            
            # 即将举行的大型赛事
            big_events_section = soup.find("div", class_="big-events")
            if big_events_section:
                for event_elem in big_events_section.find_all("a", class_="big-event"):
                    try:
                        href = event_elem.get("href", "")
                        event_id = self._extract_id_from_url(href)
                        
                        name_elem = event_elem.find("div", class_="big-event-name")
                        title = name_elem.get_text(strip=True) if name_elem else ""
                        
                        # 获取日期
                        date_elems = event_elem.find_all("span", {"data-time-format": True})
                        start_date = ""
                        end_date = ""
                        if len(date_elems) >= 2:
                            start_date = self._format_date(date_elems[0].get("data-unix", ""))
                            end_date = self._format_date(date_elems[1].get("data-unix", ""))
                        
                        if event_id and title:
                            events.append(EventInfo(
                                id=event_id,
                                title=title,
                                start_date=start_date,
                                end_date=end_date,
                                is_ongoing=False
                            ))
                    except Exception as e:
                        logger.warning(f"[HLTV] 解析即将举行赛事失败: {e}")
                        continue
            
            # 如果以上方法都没找到，尝试通用方法
            if not events:
                event_holders = soup.find_all("a", href=re.compile(r"/events/\d+/"))
                seen_ids = set()
                for elem in event_holders[:30]:
                    try:
                        href = elem.get("href", "")
                        event_id = self._extract_id_from_url(href)
                        if event_id and event_id not in seen_ids:
                            seen_ids.add(event_id)
                            # 尝试获取名称
                            name_elem = elem.find(class_=re.compile(r"event.*name", re.I))
                            if not name_elem:
                                name_elem = elem.find("div")
                            title = name_elem.get_text(strip=True) if name_elem else f"Event {event_id}"
                            
                            events.append(EventInfo(
                                id=event_id,
                                title=title,
                                start_date="",
                                end_date="",
                                is_ongoing=False
                            ))
                    except Exception:
                        continue
            
            logger.info(f"[HLTV] 获取到 {len(events)} 个赛事")
                        
        except Exception as e:
            logger.error(f"[HLTV] 解析赛事列表失败: {e}")
        
        return events
    
    def _extract_id_from_url(self, url: str) -> str:
        """从 URL 中提取 ID"""
        match = re.search(r"/(\d+)/", url)
        return match.group(1) if match else ""
    
    def _format_date(self, unix_timestamp: str) -> str:
        """格式化 Unix 时间戳为日期字符串 (月-日)"""
        try:
            if unix_timestamp:
                ts = int(unix_timestamp) / 1000  # HLTV 使用毫秒
                dt = datetime.fromtimestamp(ts, self._tz)
                return dt.strftime("%m-%d")  # 月份在前，日期在后
        except Exception:
            pass
        return ""
    
    def _format_time(self, unix_timestamp: str) -> str:
        """格式化 Unix 时间戳为时间字符串 (HH:MM)"""
        try:
            if unix_timestamp:
                ts = int(unix_timestamp) / 1000  # HLTV 使用毫秒
                dt = datetime.fromtimestamp(ts, self._tz)
                return dt.strftime("%H:%M")
        except Exception:
            pass
        return ""
    
    async def get_event_info(self, event_id: str, event_title: str = "") -> Optional[EventInfo]:
        """获取赛事详细信息"""
        title_slug = event_title.lower().replace(" ", "-") if event_title else "event"
        url = f"{self.BASE_URL}/events/{event_id}/{title_slug}"
        
        html = await self._fetch(url)
        if not html:
            return None
        
        soup = BeautifulSoup(html, "lxml")
        
        try:
            # 获取标题
            title_elem = soup.find("h1", class_="event-hub-title")
            title = title_elem.get_text(strip=True) if title_elem else event_title
            
            # 获取日期
            date_elem = soup.find("td", class_="eventdate")
            start_date = ""
            end_date = ""
            if date_elem:
                spans = date_elem.find_all("span", {"data-unix": True})
                if len(spans) >= 2:
                    start_date = self._format_date(spans[0].get("data-unix", ""))
                    end_date = self._format_date(spans[1].get("data-unix", ""))
            
            # 获取奖金
            prize_elem = soup.find("td", class_="prizepool")
            prize = prize_elem.get_text(strip=True) if prize_elem else ""
            
            # 获取队伍数
            teams_elem = soup.find("td", class_="teamsNumber")
            teams = teams_elem.get_text(strip=True) if teams_elem else ""
            
            # 获取地点
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
                is_ongoing=self._is_ongoing(start_date, end_date)
            )
            
        except Exception as e:
            logger.error(f"[HLTV] 解析赛事信息失败: {e}")
            return None
    
    def _is_ongoing(self, start_date: str, end_date: str) -> bool:
        """判断赛事是否正在进行"""
        try:
            now = datetime.now(self._tz)
            current_year = now.year
            
            # 解析开始日期 (格式: "02-08" -> month-day)
            if start_date and "-" in start_date:
                start_parts = start_date.split("-")
                start_month, start_day = int(start_parts[0]), int(start_parts[1])
                start = datetime(current_year, start_month, start_day, tzinfo=self._tz)
            else:
                return False
            
            # 解析结束日期
            if end_date and "-" in end_date:
                end_parts = end_date.split("-")
                end_month, end_day = int(end_parts[0]), int(end_parts[1])
                end = datetime(current_year, end_month, end_day, 23, 59, 59, tzinfo=self._tz)
            else:
                return False
            
            return start <= now <= end
            
        except Exception:
            return False
    
    async def get_event_matches(self, event_id: str, days: int = 7) -> list[MatchInfo]:
        """获取赛事的比赛列表（过滤 TBD）"""
        # 使用赛事专属的 matches 页面
        url = f"{self.BASE_URL}/events/{event_id}/matches"
        html = await self._fetch(url)
        
        if not html:
            return []
        
        matches = []
        soup = BeautifulSoup(html, "lxml")
        
        try:
            # 方法1: 使用 match-wrapper 结构（最精确）
            match_wrappers = soup.find_all("div", class_="match-wrapper")
            
            for wrapper in match_wrappers:
                try:
                    # 从 data 属性获取基本信息
                    match_id = wrapper.get("data-match-id", "")
                    team1_id = wrapper.get("team1", "")
                    team2_id = wrapper.get("team2", "")
                    stars = wrapper.get("data-stars", "0")
                    is_live = wrapper.get("live", "false") == "true"
                    
                    if not match_id:
                        continue
                    
                    # 检查是否是 TBD（team1 或 team2 属性为空）
                    if not team1_id or not team2_id:
                        # TBD 比赛，跳过
                        continue
                    
                    # 获取队伍名 - 从 .match-teamname 元素
                    team1 = ""
                    team2 = ""
                    
                    team_elems = wrapper.find_all("div", class_="match-teamname")
                    if len(team_elems) >= 2:
                        team1 = team_elems[0].get_text(strip=True)
                        team2 = team_elems[1].get_text(strip=True)
                    
                    # 备用：从链接的 href 解析
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
                    
                    # 获取时间 - 使用 Unix 时间戳转换为北京时间
                    match_time = ""
                    match_date = ""
                    time_elem = wrapper.find("div", class_="match-time")
                    if time_elem:
                        unix_ts = time_elem.get("data-unix", "")
                        if unix_ts:
                            # 使用时间戳转换为北京时间
                            match_time = self._format_time(unix_ts)
                            match_date = self._format_date(unix_ts)
                        else:
                            # 回退到直接获取文本
                            match_time = time_elem.get_text(strip=True)
                    
                    # 获取赛制 (bo1/bo3/bo5) - 只存储数字
                    maps_format = ""
                    meta_elem = wrapper.find("div", class_="match-meta")
                    if meta_elem:
                        meta_text = meta_elem.get_text(strip=True).lower()
                        # 匹配 bo1, bo3, bo5 等，只提取数字
                        bo_match = re.search(r"bo(\d)", meta_text)
                        if bo_match:
                            maps_format = bo_match.group(1)  # 只存储数字，如 "3"
                    
                    # 获取星级评分
                    rating = 0
                    try:
                        rating = int(stars) if stars else 0
                    except ValueError:
                        rating = 0
                    
                    matches.append(MatchInfo(
                        id=match_id,
                        date=match_date if not is_live else "LIVE",
                        time=match_time if not is_live else "LIVE",
                        team1=team1,
                        team2=team2,
                        team1_id=team1_id,
                        team2_id=team2_id,
                        maps=maps_format,
                        rating=rating,
                        is_live=is_live
                    ))
                        
                except Exception as e:
                    logger.debug(f"[HLTV] 解析单个 match-wrapper 失败: {e}")
                    continue
            
            # 方法2: 如果没找到 match-wrapper，回退到链接解析
            if not matches:
                logger.debug("[HLTV] 未找到 match-wrapper，尝试链接解析")
                match_links = soup.find_all("a", href=re.compile(r"/matches/\d+/"))
                seen_ids = set()
                
                for link in match_links:
                    try:
                        href = link.get("href", "")
                        match_id = self._extract_id_from_url(href)
                        
                        if not match_id or match_id in seen_ids:
                            continue
                        seen_ids.add(match_id)
                        
                        # 从 href 解析队伍名
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
                        
                        matches.append(MatchInfo(
                            id=match_id,
                            date="LIVE" if is_live else "",
                            time="LIVE" if is_live else "",
                            team1=team1,
                            team2=team2,
                            team1_id="",
                            team2_id="",
                            maps="",
                            rating=0,
                            is_live=is_live
                        ))
                            
                    except Exception as e:
                        logger.debug(f"[HLTV] 解析单个比赛链接失败: {e}")
                        continue
            
            logger.info(f"[HLTV] 获取到 {len(matches)} 场比赛")
                        
        except Exception as e:
            logger.error(f"[HLTV] 解析比赛列表失败: {e}")
        
        return matches
    
    async def get_event_results(self, event_id: str, days: int = 7, max_results: int = 20) -> list[ResultInfo]:
        """获取赛事的已结束比赛结果"""
        url = f"{self.BASE_URL}/results?event={event_id}"
        html = await self._fetch(url)
        
        if not html:
            return []
        
        results = []
        soup = BeautifulSoup(html, "lxml")
        
        try:
            # 查找所有比赛结果容器 (result-con)
            result_containers = soup.find_all("div", class_="result-con")
            
            for container in result_containers[:max_results]:
                try:
                    # 获取链接和比赛 ID
                    link = container.find("a", href=re.compile(r"/matches/\d+/"))
                    if not link:
                        continue
                    
                    href = link.get("href", "")
                    match_id = self._extract_id_from_url(href)
                    
                    if not match_id:
                        continue
                    
                    # 获取队伍名 - 使用 team-cell 中的 .team 类
                    team_cells = link.find_all("td", class_="team-cell")
                    team1 = ""
                    team2 = ""
                    
                    if len(team_cells) >= 2:
                        team1_elem = team_cells[0].find("div", class_="team")
                        team2_elem = team_cells[1].find("div", class_="team")
                        team1 = team1_elem.get_text(strip=True) if team1_elem else ""
                        team2 = team2_elem.get_text(strip=True) if team2_elem else ""
                    
                    # 如果没找到，从 href 解析
                    if not team1 or not team2:
                        match = re.search(r"/([^/]+)-vs-([^/]+)-", href)
                        if match:
                            team1 = team1 or match.group(1).replace("-", " ").title()
                            team2 = team2 or match.group(2).replace("-", " ").title()
                    
                    # 获取比分 - 使用 result-score 中的 score-won 和 score-lost
                    score_cell = link.find("td", class_="result-score")
                    score1 = "0"
                    score2 = "0"
                    
                    if score_cell:
                        score_won = score_cell.find("span", class_="score-won")
                        score_lost = score_cell.find("span", class_="score-lost")
                        
                        # 判断哪个队伍赢了
                        team1_cell = team_cells[0] if len(team_cells) >= 2 else None
                        team1_won = False
                        if team1_cell:
                            team1_div = team1_cell.find("div", class_="team")
                            if team1_div and "team-won" in team1_div.get("class", []):
                                team1_won = True
                        
                        if team1_won:
                            score1 = score_won.get_text(strip=True) if score_won else "0"
                            score2 = score_lost.get_text(strip=True) if score_lost else "0"
                        else:
                            score1 = score_lost.get_text(strip=True) if score_lost else "0"
                            score2 = score_won.get_text(strip=True) if score_won else "0"
                    
                    if team1 and team2:
                        results.append(ResultInfo(
                            id=match_id,
                            date="",
                            team1=team1,
                            team2=team2,
                            score1=score1,
                            score2=score2
                        ))
                        
                except Exception as e:
                    logger.debug(f"[HLTV] 解析单个结果失败: {e}")
                    continue
            
            logger.info(f"[HLTV] 获取到 {len(results)} 个比赛结果")
                            
        except Exception as e:
            logger.error(f"[HLTV] 解析结果列表失败: {e}")
        
        return results
    
    async def get_match_stats(self, match_id: str, team1: str = "", team2: str = "",
                              event_title: str = "") -> Optional[MatchStats]:
        """获取比赛详细数据"""
        # 构建 URL
        t1_slug = team1.lower().replace(" ", "-") if team1 else "team1"
        t2_slug = team2.lower().replace(" ", "-") if team2 else "team2"
        event_slug = event_title.lower().replace(" ", "-") if event_title else "event"
        
        url = f"{self.BASE_URL}/matches/{match_id}/{t1_slug}-vs-{t2_slug}-{event_slug}"
        logger.info(f"[HLTV] 获取比赛数据: {url}")
        
        html = await self._fetch(url)
        
        if not html:
            return None
        
        soup = BeautifulSoup(html, "lxml")
        
        try:
            # 获取队伍名
            team_names = soup.find_all("div", class_="teamName")
            if len(team_names) >= 2:
                team1 = team1 or team_names[0].get_text(strip=True)
                team2 = team2 or team_names[1].get_text(strip=True)
            
            logger.debug(f"[HLTV] 队伍: {team1} vs {team2}")
            
            # 获取比分 - 查找正确的元素
            score1 = "0"
            score2 = "0"
            
            # 尝试多种方式获取比分
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
            
            # 备用方法：查找包含比分的元素
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
            
            logger.debug(f"[HLTV] 比分: {score1} - {score2}")
            
            # 获取状态
            status = "完成"
            countdown = soup.find("div", class_="countdown")
            if countdown:
                status = countdown.get_text(strip=True)
            
            # 解析 Veto/BP
            vetos = []
            veto_boxes = soup.find_all("div", class_="veto-box")
            for box in veto_boxes:
                # 提取文本，按行分割，去除空行和冗余信息
                lines = [line.strip() for line in box.get_text("\n").split("\n") if line.strip()]
                # 简单的过滤，保留有意义的 BP 信息
                filtered_lines = []
                for line in lines:
                    if not line.startswith("*"):  # 过滤掉一些注释
                        filtered_lines.append(line)
                if filtered_lines:
                    vetos.extend(filtered_lines)
            
            # 解析地图数据
            maps = []
            map_holders = soup.find_all("div", class_="mapholder")
            
            for map_holder in map_holders:
                try:
                    # 获取地图名
                    map_name_elem = map_holder.find("div", class_="mapname")
                    map_name = map_name_elem.get_text(strip=True) if map_name_elem else ""
                    
                    if not map_name or map_name == "TBA":
                        continue
                    
                    # 获取地图比分 - 查找 results-left 和 results-right
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
                    
                    # 备用方式：查找所有 results-team-score
                    if map_score1 == "-" and map_score2 == "-":
                        score_elems = map_holder.find_all(class_="results-team-score")
                        if len(score_elems) >= 2:
                            map_score1 = score_elems[0].get_text(strip=True)
                            map_score2 = score_elems[1].get_text(strip=True)
                    
                    # 清理比分（确保只有数字）
                    map_score1 = re.sub(r'[^\d-]', '', map_score1) or "-"
                    map_score2 = re.sub(r'[^\d-]', '', map_score2) or "-"
                    
                    # 获取 pick 信息
                    pick_by = ""
                    pick_elem = map_holder.find(class_=re.compile(r"pick|picked"))
                    if pick_elem:
                        pick_text = pick_elem.get_text(strip=True)
                        if team1 and team1.lower() in pick_text.lower():
                            pick_by = team1
                        elif team2 and team2.lower() in pick_text.lower():
                            pick_by = team2
                    
                    maps.append(MapStats(
                        map_name=map_name,
                        pick_by=pick_by,
                        score_team1=map_score1,
                        score_team2=map_score2
                    ))
                except Exception as e:
                    logger.debug(f"[HLTV] 解析地图失败: {e}")
                    continue
            
            logger.info(f"[HLTV] 地图数: {len(maps)}")
            
            # 解析选手数据
            players = []
            
            # 查找统计表格 - 只查找 totalstats 以避免获取到 hidden 的 ct/t stats
            stats_tables = soup.find_all("table", class_="totalstats")
            
            # 如果没找到 totalstats，尝试宽松搜索但过滤掉 hidden
            if not stats_tables:
                all_tables = soup.find_all("table", class_=re.compile(r"stats|totalstats"))
                stats_tables = [t for t in all_tables if "hidden" not in t.get("class", [])]

            if not stats_tables:
                # 尝试查找其他格式的选手数据
                player_rows = soup.find_all("tr", class_=re.compile(r"player"))
                if player_rows:
                    for i, row in enumerate(player_rows[:10]):
                        try:
                            player_link = row.find("a", href=re.compile(r"/player/\d+/"))
                            if not player_link:
                                continue
                            
                            player_id = self._extract_id_from_url(player_link.get("href", ""))
                            nickname = player_link.get_text(strip=True)
                            
                            # 获取数据
                            cells = row.find_all("td")
                            kd = "0-0"
                            adr = "0"
                            rating = "0"
                            
                            for cell in cells:
                                cell_class = cell.get("class", [])
                                cell_text = cell.get_text(strip=True)
                                
                                if "kd" in str(cell_class).lower():
                                    kd = cell_text
                                elif "adr" in str(cell_class).lower():
                                    adr = cell_text
                                elif "rating" in str(cell_class).lower():
                                    rating = cell_text
                            
                            kd_parts = kd.replace("/", "-").split("-")
                            kills = kd_parts[0] if len(kd_parts) > 0 else "0"
                            deaths = kd_parts[1] if len(kd_parts) > 1 else "0"
                            
                            players.append(PlayerStats(
                                id=player_id,
                                nickname=nickname,
                                team="team1" if i < 5 else "team2",
                                kills=kills,
                                deaths=deaths,
                                adr=adr,
                                rating=rating
                            ))
                        except Exception:
                            continue
            else:
                # 解析统计表格
                for table_idx, table in enumerate(stats_tables[:2]):
                    rows = table.find_all("tr")[1:]  # 跳过表头
                    for row in rows[:5]:  # 每队最多5个选手
                        try:
                            cells = row.find_all("td")
                            if len(cells) < 2:
                                continue
                            
                            player_link = row.find("a", href=re.compile(r"/player/\d+/"))
                            player_id = self._extract_id_from_url(player_link.get("href", "")) if player_link else ""
                            
                            # 获取选手名 - 优先获取昵称部分
                            nickname = ""
                            
                            # 尝试获取 .player-nick (通常在 flagAlign 内)
                            nick_elem = cells[0].find("span", class_="player-nick")
                            if nick_elem:
                                nickname = nick_elem.get_text(strip=True)
                            
                            # 尝试获取 .smartphone-only (通常只包含昵称)
                            if not nickname:
                                mobile_name = cells[0].find("div", class_="smartphone-only")
                                if mobile_name:
                                    nickname = mobile_name.get_text(strip=True)
                            
                            # 如果还没找到，尝试从链接文本获取（可能包含多余信息，但比 get_text 好）
                            if not nickname and player_link:
                                # 有些页面结构不一样，链接文本可能就是昵称
                                # 但在这里通常包含全名，所以这是一个备选
                                nickname = player_link.get_text(strip=True)
                                
                            # 最后回退到单元格文本
                            if not nickname:
                                nickname = cells[0].get_text(strip=True)
                            
                            # 获取 K-D
                            kd = cells[1].get_text(strip=True) if len(cells) > 1 else "0-0"
                            kd_parts = kd.replace("/", "-").split("-")
                            kills = kd_parts[0].strip() if len(kd_parts) > 0 else "0"
                            deaths = kd_parts[1].strip() if len(kd_parts) > 1 else "0"
                            
                            # ADR 和 Rating
                            adr = cells[2].get_text(strip=True) if len(cells) > 2 else "0"
                            rating = cells[-1].get_text(strip=True) if len(cells) > 3 else "0"
                            
                            # 清理数据
                            try:
                                if not kills.replace("-", "").isdigit():
                                    kills = "0"
                                if not deaths.replace("-", "").isdigit():
                                    deaths = "0"
                            except:
                                kills = "0"
                                deaths = "0"
                            
                            players.append(PlayerStats(
                                id=player_id,
                                nickname=nickname,
                                team="team1" if table_idx == 0 else "team2",
                                kills=kills,
                                deaths=deaths,
                                adr=adr,
                                rating=rating
                            ))
                        except Exception as e:
                            logger.debug(f"[HLTV] 解析选手数据失败: {e}")
                            continue
            
            logger.info(f"[HLTV] 选手数: {len(players)}")
            
            return MatchStats(
                match_id=match_id,
                team1=team1,
                team2=team2,
                score1=score1,
                score2=score2,
                status=status,
                maps=maps,
                players=players,
                vetos=vetos,
                event=event_title
            )
            
        except Exception as e:
            logger.error(f"[HLTV] 解析比赛数据失败: {e}")
            return None
    
    async def get_latest_result_with_stats(self, event_id: str, event_title: str = "") -> Optional[MatchStats]:
        """获取最近一场比赛的详细数据"""
        # 先获取最近的结果
        results = await self.get_event_results(event_id, max_results=1)
        
        if not results:
            logger.warning(f"[HLTV] 没有找到赛事 {event_id} 的比赛结果")
            return None
        
        result = results[0]
        logger.info(f"[HLTV] 获取最近比赛: {result.team1} vs {result.team2}")
        
        # 获取详细数据
        stats = await self.get_match_stats(
            result.id,
            team1=result.team1,
            team2=result.team2,
            event_title=event_title
        )
        
        return stats


# 全局实例
hltv_data = HLTVDataSource()
