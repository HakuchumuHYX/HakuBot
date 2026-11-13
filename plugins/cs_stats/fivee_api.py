"""5E 对战平台的网页接口，全部免登录。

生涯数字以 gate 的 player_career 为准；arena 的统计口径和它对不上（场次、胜率都不同），
只用来补 player_career 没有的字段（闪光、KAST、残局、下包拆包）和近 10 场的对局号。
arena 逐场的 Rating 是 1.0 版，gate 列表和单局详情是 2.0 版，所以那 10 场的数据都从 gate 取。
player_career 里的 ELO 和段位是最佳赛季的，当前赛季的要从 v3/player/home 取。
"""

import httpx

from plugins.cs_stats.config import plugin_config

GATE = "https://gate.5eplay.com"
ARENA = "https://arena.5eplay.com"
AVATAR_BASE = "https://oss-arena.5eplay.com/"
LEVEL_ICON_BASE = AVATAR_BASE + "ya_static/images/common/"
# 优先排位；arena 接口不带这个参数时返回的是别的模式
MATCH_TYPE = 9
# 段位接口只给编号，名字按 5E 网页的段位表：level_type 1 是 2025 版（21～30），2 是 2026 版（41～55）。
# 2026 版 51 往上都是 S，靠图标和星数区分，接口也不给名字
LEVELS_2025 = dict(enumerate(("D", "D+", "C", "C+", "B", "B+", "A", "A+", "S", "S+"), start=21))
LEVELS_2026 = dict(enumerate(("D", "C", "C+", "C++", "B", "B+", "B++", "A", "A+", "A++"), start=41))
LEVELS_2026.update({level_id: "S" for level_id in range(51, 56)})
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


class FiveEError(Exception):
    """文案直接回给用户"""


def avatar_url(path: str) -> str:
    if not path or path.startswith("http"):
        return path or ""
    return AVATAR_BASE + path


def level_name(level_type: int, level_id: int) -> str:
    return (LEVELS_2025 if level_type == 1 else LEVELS_2026).get(level_id, "")


def level_icon(level_type: int, level_id: int | None) -> str:
    """level_id 为 None 表示定级中。2025 版图标按名字命名（“+”写成“2”），2026 版按编号"""
    if level_id is None:
        return LEVEL_ICON_BASE + "level_2026/ques.avif"
    if level_type == 1:
        return f"{LEVEL_ICON_BASE}level_2025/{level_name(1, level_id).replace('+', '2')}.png"
    return f"{LEVEL_ICON_BASE}level_2026/Level_{level_id}.avif"


async def _request(method: str, url: str, **kwargs) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=plugin_config.http_timeout) as client:
        resp = await client.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()


def _gate_data(data: dict, what: str) -> dict:
    if data.get("code") != 0 or not data.get("data"):
        raise FiveEError(f"{what}失败：{data.get('message') or data.get('code')}")
    return data["data"]


async def search(keyword: str) -> list[dict]:
    data = await _request(
        "GET", f"{ARENA}/api/search/player/1/16", params={"keywords": keyword}
    )
    users = ((data.get("data") or {}).get("user") or {}).get("list") or []
    return [
        {
            "id": user["domain"],
            "nickname": user.get("username") or user["domain"],
            "avatar": avatar_url(user.get("avatar_url") or ""),
        }
        for user in users
        if user.get("domain")
    ]


async def uuid_of(domain: str) -> str:
    data = await _request(
        "POST",
        f"{GATE}/userinterface/http/v1/userinterface/idTransfer",
        json={"trans": {"domain": domain}},
    )
    uuid = ((data.get("data") or {}).get("uuid")) or ""
    if not uuid:
        raise FiveEError(f"没有找到 5E 玩家 {domain}")
    return uuid


async def career(uuid: str) -> dict:
    data = await _request("GET", f"{GATE}/crane/http/api/data/player_career", params={"uuid": uuid})
    return _gate_data(data, "5E 生涯")["career_data"]


async def home(uuid: str) -> dict:
    """当前赛季在 elo_info.modes["9"]，最佳赛季在 career"""
    data = await _request("GET", f"{GATE}/crane/http/api/data/v3/player/home", params={"uuid": uuid})
    return _gate_data(data, "5E 主页")


async def matches(uuid: str) -> list[dict]:
    data = await _request("GET", f"{GATE}/crane/http/api/data/player_match", params={"uuid": uuid})
    return _gate_data(data, "5E 对局列表").get("match_data") or []


async def arena(domain: str) -> dict:
    """返回整个响应：生涯卡要用顶层的 data 和 match 两块"""
    data = await _request(
        "GET", f"{ARENA}/api/data/player/{domain}", params={"match_type": MATCH_TYPE}
    )
    if not data.get("success") or not data.get("data"):
        raise FiveEError(f"5E 生涯补充数据失败：{data.get('message') or data.get('errcode')}")
    return data


async def match_detail(match_id: str) -> dict:
    data = await _request("GET", f"{GATE}/crane/http/api/data/match/{match_id}")
    return _gate_data(data, "5E 单局详情")
