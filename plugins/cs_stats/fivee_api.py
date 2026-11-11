"""5E 对战平台的网页接口，全部免登录。

生涯数字以 gate 的 player_career 为准；arena 的统计口径和它对不上（场次、胜率都不同），
只用来补 player_career 没有的字段（闪光、KAST、残局、下包拆包、近 10 场走势）。
"""

import asyncio

import httpx

from plugins.cs_stats.config import plugin_config

GATE = "https://gate.5eplay.com"
ARENA = "https://arena.5eplay.com"
AVATAR_BASE = "https://oss-arena.5eplay.com/"
# 优先排位；arena 接口不带这个参数时返回的是别的模式
MATCH_TYPE = 9
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


async def matches(uuid: str) -> list[dict]:
    data = await _request("GET", f"{GATE}/crane/http/api/data/player_match", params={"uuid": uuid})
    return _gate_data(data, "5E 对局列表").get("match_data") or []


async def arena(domain: str) -> dict:
    data = await _request(
        "GET", f"{ARENA}/api/data/player/{domain}", params={"match_type": MATCH_TYPE}
    )
    if not data.get("success") or not data.get("data"):
        raise FiveEError(f"5E 生涯补充数据失败：{data.get('message') or data.get('errcode')}")
    return data


async def match_detail(match_id: str) -> dict:
    data = await _request("GET", f"{GATE}/crane/http/api/data/match/{match_id}")
    return _gate_data(data, "5E 单局详情")


async def profile_bundle(domain: str, uuid: str) -> tuple[dict, list[dict], dict]:
    """生涯卡要的三份数据并发拉"""
    return await asyncio.gather(career(uuid), matches(uuid), arena(domain))
