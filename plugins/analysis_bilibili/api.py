"""B 站接口请求：带 bili_ticket 取 JSON、读短链跳转地址"""

import json

from aiohttp import ClientTimeout

from utils.bilibili import HEADERS, get_ticket
from utils.network.http import HttpError, get_client_session

# 被动解析，接口卡住就算了，不必等满默认的 120 秒
TIMEOUT = ClientTimeout(total=20, connect=8)


async def get_json(url: str, **params) -> dict:
    """返回接口的 data（番剧接口叫 result）；code 非 0 视为失败"""
    async with get_client_session().get(
        url,
        params=params,
        headers=HEADERS,
        cookies={"bili_ticket": await get_ticket()},
        timeout=TIMEOUT,
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise HttpError(resp.status, body[:80])
    payload = json.loads(body)
    if payload["code"] != 0:
        raise RuntimeError(f"{url} 返回 {payload['code']}: {payload.get('message')}")
    return payload.get("data") or payload["result"]


async def resolve_short_link(link: str) -> str:
    """只读跳转地址，不去请求跳转后的页面"""
    async with get_client_session().get(
        f"https://{link}", headers=HEADERS, allow_redirects=False, timeout=TIMEOUT
    ) as resp:
        location = resp.headers.get("Location")
    if not location:
        raise HttpError(resp.status, f"短链 {link} 没有跳转地址")
    return location
