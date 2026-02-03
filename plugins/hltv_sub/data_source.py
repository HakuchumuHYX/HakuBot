"""HLTV 数据源 - 使用 curl_cffi 绕过 Cloudflare"""

import re
import asyncio
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
import pytz
from bs4 import BeautifulSoup, Tag

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
    stats_id: str = ""  # 用于关联单图详细数据


@dataclass
class PlayerStats:
    """选手数据"""
    id: str
    nickname: str
    team: str  # team1 or team2
    kills: str
    deaths: str
    adr: str
    kast: str
    rating: str
    swing: str = ""


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
    players: list[PlayerStats]  # 总数据
    map_stats_details: Dict[str, List[PlayerStats]] = field(default_factory=dict)  # 单图详细数据 {map_name: players}
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
    
    def _parse_player_table(self, table: Tag, team_idx: int) -> List[PlayerStats]:
        """解析选手数据表格，动态查找列"""
        players = []
        
        try:
            # 1. 解析表头，确定列索引
            headers = []
            header_row = table.find("tr", class_="header-row")
            if not header_row and table.find("thead"):
                header_row = table.find("thead").find("tr")
            if not header_row:
                header_row = table.find("tr")
            
            if not header_row:
                return []
            
            # 获取所有单元格文本（包括隐藏的）
            cols = header_row.find_all(["th", "td"])
            headers = [c.get_text(strip=True).upper() for c in cols]
            
            # 确定关键列的索引
            idx_kd = -1
            idx_adr = -1
            idx_rating = -1
            idx_swing = -1
            idx_kast = -1
            
            for i, h in enumerate(headers):
                if "K-D" in h:
                    idx_kd = i
                elif "ADR" in h and "EADR" not in h: # 避免匹配到 eADR
                    idx_adr = i
                elif "RATING" in h:
                    idx_rating = i
                elif "SWING" in h:
                    idx_swing = i
                elif "KAST" in h and "EKAST" not in h:
                    idx_kast = i
            
            # 如果没找到，尝试默认值（虽然不太可能，作为回退）
            if idx_kd == -1 and len(headers) > 1: idx_kd = 1
            if idx_adr == -1 and len(headers) > 4: idx_adr = 4
            if idx_rating == -1: idx_rating = len(headers) - 1
            
            # 2. 解析数据行
            rows = table.find_all("tr")
            # 过滤掉表头行
            data_rows = [r for r in rows if not r.find("th") and "header-row" not in r.get("class", [])]
            
            for row in data_rows[:5]:  # 每队最多5人
                cells = row.find_all("td")
                if not cells:
                    continue
                
                # 获取选手信息
                player_link = row.find("a", href=re.compile(r"/player/\d+/"))
                player_id = self._extract_id_from_url(player_link.get("href", "")) if player_link else ""
                
                # 获取昵称
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
                
                # 获取数据
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
                
                # 处理 K-D
                kd_parts = kd.replace("/", "-").split("-")
                kills = kd_parts[0].strip() if len(kd_parts) > 0 else "0"
                deaths = kd_parts[1].strip() if len(kd_parts) > 1 else "0"
                
                # 简单清理
                if not kills.replace("-", "").isdigit(): kills = "0"
                if not deaths.replace("-", "").isdigit(): deaths = "0"
                
                players.append(PlayerStats(
                    id=player_id,
                    nickname=nickname,
                    team="team1" if team_idx == 0 else "team2",
                    kills=kills,
                    deaths=deaths,
                    adr=adr,
                    rating=rating,
                    swing=swing,
                    kast=kast
                ))
                
        except Exception as e:
            logger.debug(f"[HLTV] 解析表格失败: {e}")
            
        return players

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
            
            # 获取比分
            score1 = "0"
            score2 = "0"
            team_boxes = soup.find_all("div", class_="team")
            for i, box in enumerate(team_boxes[:2]):
                score_elem = box.find(class_=re.compile(r"score|won|lost"))
                if score_elem:
                    score_text = score_elem.get_text(strip=True)
                    if score_text.isdigit():
                        if i == 0: score1 = score_text
                        else: score2 = score_text
            
            if score1 == "0" and score2 == "0":
                score_elems = soup.find_all(class_=re.compile(r"score$|won$|lost$"))
                for elem in score_elems:
                    text = elem.get_text(strip=True)
                    if text.isdigit():
                        if score1 == "0": score1 = text
                        elif score2 == "0": 
                            score2 = text
                            break
            
            # 获取状态
            status = "完成"
            countdown = soup.find("div", class_="countdown")
            if countdown:
                status = countdown.get_text(strip=True)
            
            # 解析 Veto/BP
            vetos = []
            veto_boxes = soup.find_all("div", class_="veto-box")
            for box in veto_boxes:
                lines = [line.strip() for line in box.get_text("\n").split("\n") if line.strip()]
                filtered_lines = [l for l in lines if not l.startswith("*")]
                if filtered_lines:
                    vetos.extend(filtered_lines)
            
            # 解析地图列表和关联的 Stats ID
            maps = []
            map_holders = soup.find_all("div", class_="mapholder")
            
            for map_holder in map_holders:
                try:
                    map_name_elem = map_holder.find("div", class_="mapname")
                    map_name = map_name_elem.get_text(strip=True) if map_name_elem else ""
                    
                    if not map_name or map_name == "TBA":
                        continue
                    
                    # 获取 stats ID - 更激进的匹配方式
                    stats_id = ""
                    # 先尝试查找链接
                    stats_link = map_holder.find("a", href=re.compile(r"mapstatsid/(\d+)"))
                    if stats_link:
                        match = re.search(r"mapstatsid/(\d+)", stats_link.get("href", ""))
                        if match:
                            stats_id = match.group(1)
                    
                    # 如果没找到，直接在 HTML 中搜索
                    if not stats_id:
                        html_str = str(map_holder)
                        match = re.search(r"mapstatsid/(\d+)", html_str)
                        if match:
                            stats_id = match.group(1)
                    
                    logger.info(f"[HLTV] Map: {map_name}, Stats ID: {stats_id}")
                    
                    # 获取地图比分
                    map_score1 = "-"
                    map_score2 = "-"
                    
                    results_left = map_holder.find(["div", "span"], class_="results-left")
                    results_right = map_holder.find(["div", "span"], class_="results-right")
                    
                    if results_left:
                        score_elem = results_left.find("div", class_="results-team-score")
                        if score_elem: map_score1 = score_elem.get_text(strip=True)
                    
                    if results_right:
                        score_elem = results_right.find("div", class_="results-team-score")
                        if score_elem: map_score2 = score_elem.get_text(strip=True)
                    
                    if map_score1 == "-" and map_score2 == "-":
                        score_elems = map_holder.find_all(class_="results-team-score")
                        if len(score_elems) >= 2:
                            map_score1 = score_elems[0].get_text(strip=True)
                            map_score2 = score_elems[1].get_text(strip=True)
                    
                    map_score1 = re.sub(r'[^\d-]', '', map_score1) or "-"
                    map_score2 = re.sub(r'[^\d-]', '', map_score2) or "-"
                    
                    # Pick info
                    pick_by = ""
                    pick_elem = map_holder.find(class_=re.compile(r"pick|picked"))
                    if pick_elem:
                        pick_text = pick_elem.get_text(strip=True)
                        if team1 and team1.lower() in pick_text.lower(): pick_by = team1
                        elif team2 and team2.lower() in pick_text.lower(): pick_by = team2
                    
                    maps.append(MapStats(
                        map_name=map_name,
                        pick_by=pick_by,
                        score_team1=map_score1,
                        score_team2=map_score2,
                        stats_id=stats_id
                    ))
                except Exception as e:
                    logger.debug(f"[HLTV] 解析地图失败: {e}")
                    continue
            
            logger.info(f"[HLTV] 地图数: {len(maps)}")
            
            # 解析总选手数据 (id="all-content")
            total_players = []
            all_content = soup.find("div", id="all-content")
            if all_content:
                stats_tables = all_content.find_all("table", class_="totalstats")
                for idx, table in enumerate(stats_tables[:2]):
                    team_players = self._parse_player_table(table, idx)
                    total_players.extend(team_players)
            else:
                # 回退旧逻辑
                stats_tables = soup.find_all("table", class_="totalstats")
                if not stats_tables:
                    all_tables = soup.find_all("table", class_=re.compile(r"stats|totalstats"))
                    stats_tables = [t for t in all_tables if "hidden" not in t.get("class", [])]
                
                for idx, table in enumerate(stats_tables[:2]):
                    team_players = self._parse_player_table(table, idx)
                    total_players.extend(team_players)
            
            logger.info(f"[HLTV] 总选手数: {len(total_players)}")
            
            # 解析单图选手数据
            map_stats_details = {}
            for map_info in maps:
                if not map_info.stats_id:
                    continue
                
                # 查找对应的 div ID (例如: "12345-content")
                content_id = f"{map_info.stats_id}-content"
                content_div = soup.find("div", id=content_id)
                
                if content_div:
                    map_players = []
                    # 通常单图里面也是 table.totalstats 或者 table.stats-table
                    tables = content_div.find_all("table", class_=re.compile(r"stats-table|totalstats"))
                    for idx, table in enumerate(tables[:2]):
                        team_players = self._parse_player_table(table, idx)
                        map_players.extend(team_players)
                    
                    if map_players:
                        map_stats_details[map_info.map_name] = map_players
                        logger.info(f"[HLTV] 解析地图 {map_info.map_name} 数据: {len(map_players)} 名选手")
            
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
