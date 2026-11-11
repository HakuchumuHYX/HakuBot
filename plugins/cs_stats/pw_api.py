"""完美电竞 App 的私有接口。战绩类接口用 bot 主自己登录的 token 访问，搜索不用登录。

dataSource：3 是完美天梯，1 是官匹（完美同步的 Steam 官方匹配）。
头像不用完美的，取 Steam 资料页上的，见 steam_avatar。
"""

import xml.etree.ElementTree as ET

import httpx
from nonebot import logger

from plugins.cs_stats.config import plugin_config
from utils.logging import get_exc_desc

API = "https://api.wmpvp.com/api"
SEARCH_URL = "https://appengine.wmpvp.com/steamcn/app/search/user"
SOURCE = {"pvp": 3, "mm": 1}
# 无效 token 实测返回 statusCode 4001「登录失效，请重新登录」
TOKEN_INVALID_CODE = 4001


class PWError(Exception):
    """文案直接回给用户"""


class PWTokenInvalid(PWError):
    def __init__(self, newly: bool):
        super().__init__("完美查询暂时不可用（登录态失效，已通知 bot 主）")
        self.newly = newly


token_invalid = False


def reset_token_state() -> None:
    global token_invalid
    token_invalid = False


async def _post(url: str, payload: dict, *, auth: bool = True) -> dict:
    if auth and token_invalid:
        raise PWTokenInvalid(newly=False)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "okhttp/4.11.0",
        "appversion": plugin_config.pw_appversion,
        "platform": "android",
    }
    if auth:
        headers["token"] = plugin_config.pw_token
    async with httpx.AsyncClient(timeout=plugin_config.http_timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _unwrap(data: dict, what: str):
    global token_invalid
    if data.get("statusCode") == 0:
        return data.get("data")
    if data.get("statusCode") == TOKEN_INVALID_CODE:
        newly = not token_invalid
        token_invalid = True
        raise PWTokenInvalid(newly=newly)
    raise PWError(f"{what}失败：{data.get('errorMessage') or data.get('statusCode')}")


def _ids(steam_id: str) -> dict:
    return {"mySteamId": int(plugin_config.pw_steam_id), "toSteamId": int(steam_id)}


async def search(keyword: str) -> list[dict]:
    """昵称或 SteamID64 都能搜"""
    data = await _post(SEARCH_URL, {"keyword": keyword, "page": 1}, auth=False)
    if data.get("code") != 1:
        raise PWError(f"完美搜索失败：{data.get('message') or data.get('code')}")
    return [
        {
            "id": str(item["steamId"]),
            "nickname": item.get("pvpNickName") or item.get("appNickName") or str(item["steamId"]),
            "score": item.get("pvpScore") or 0,
        }
        for item in data.get("result") or []
        if item.get("steamId")
    ]


async def _pvp_season(steam_id: str, season: str) -> dict:
    payload = {**_ids(steam_id), "accessToken": "", "csgoSeasonId": season}
    return _unwrap(await _post(f"{API}/csgo/home/pvp/detailStats", payload), "完美天梯生涯")


async def pvp_stats(steam_id: str) -> dict:
    """本赛季没打就往前找有数据的赛季；都没有时返回本赛季（cnt 为 0）"""
    current = await _pvp_season(steam_id, "")
    if int(current.get("cnt") or 0) > 0:
        return current
    season_no = int(str(current["seasonId"]).lstrip("S"))
    for back in range(1, plugin_config.pvp_season_lookback + 1):
        if season_no - back <= 0:
            break
        data = await _pvp_season(steam_id, f"S{season_no - back}")
        if int(data.get("cnt") or 0) > 0:
            return data
    return current


async def official_stats(steam_id: str) -> dict:
    """官匹最近 100 局汇总"""
    payload = {**_ids(steam_id), "accessToken": "", "csgoSeasonId": ""}
    return _unwrap(await _post(f"{API}/csgo/home/official/detailStats", payload), "官匹生涯")


async def match_list(steam_id: str, mode: str, size: int = 10) -> tuple[bool, list[dict]]:
    """返回（对方是否公开数据, 最近对局）"""
    payload = {
        **_ids(steam_id),
        "dataSource": SOURCE[mode],
        "page": 1,
        "pageSize": size,
        "csgoSeasonId": "recent",
        "pvpType": -1,
    }
    data = _unwrap(await _post(f"{API}/csgo/home/match/list", payload), "对局列表")
    return bool(data.get("dataPublic")), data.get("matchList") or []


async def match_detail(match_id: str, mode: str) -> dict:
    # 原来的 home/match/detailStats 已经 404
    payload = {"matchId": match_id, "platform": "admin", "dataSource": str(SOURCE[mode])}
    return _unwrap(await _post(f"{API}/v1/csgo/match", payload), "单局详情")


async def steam_avatar(steam_id: str) -> str:
    """完美的两个图床（img/cdn.wmpvp.com）连续请求就会挂起或 403，所以头像改取 Steam 资料页。
    资料页一人一次请求，单局卡十个人就是十次，所以单局卡不放头像。
    取不到就返回空串，卡片显示首字母。"""
    url = f"https://steamcommunity.com/profiles/{steam_id}?xml=1"
    try:
        async with httpx.AsyncClient(timeout=plugin_config.http_timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        return ET.fromstring(resp.text).findtext("avatarFull") or ""
    except (httpx.HTTPError, ET.ParseError) as e:
        logger.warning(f"[cs_stats] Steam 头像获取失败 {steam_id}: {get_exc_desc(e)}")
        return ""
