"""B 站 Web 接口签名（wbi、bili_ticket），analysis_bilibili 和 bili_dyn_sub 共用

算法见 https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md
"""

import hashlib
import hmac
import time
import urllib.parse
from hashlib import md5

from utils.network.http import get_client_session

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}

# fmt: off
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]
# fmt: on

# GenWebTicket 的 web 端固定常量
_TICKET_HMAC_KEY = "XgwSnGZ1p"
_TICKET_KEY_ID = "ec02"
_TICKET_URL = (
    "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
)

# key 一天一换，缓存 1 小时足够
_WBI_KEYS_TTL = 3600
_wbi_keys = {"img_key": "", "sub_key": "", "ts": 0.0}

_TICKET_TTL = 7200
_ticket = {"ticket": "", "ts": 0.0}


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """补上 wts 和 w_rid；值统一转成字符串并去掉 !'()*"""
    orig = img_key + sub_key
    mixin_key = "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]
    params = dict(params, wts=round(time.time()))
    params = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in sorted(params.items())
    }
    query = urllib.parse.urlencode(params)
    params["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return params


def ticket_params(ts: int) -> dict[str, str]:
    """GenWebTicket 的查询参数"""
    hexsign = hmac.new(
        _TICKET_HMAC_KEY.encode(), f"ts{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "key_id": _TICKET_KEY_ID,
        "hexsign": hexsign,
        "context[ts]": str(ts),
        "csrf": "",
    }


async def get_wbi_keys() -> tuple[str, str]:
    now = time.time()
    if _wbi_keys["img_key"] and now - _wbi_keys["ts"] < _WBI_KEYS_TTL:
        return _wbi_keys["img_key"], _wbi_keys["sub_key"]
    # 未登录时 nav 返回 -101，但 data.wbi_img 照样有
    async with get_client_session().get(
        "https://api.bilibili.com/x/web-interface/nav", headers=HEADERS
    ) as resp:
        data = (await resp.json(content_type=None))["data"]["wbi_img"]
    img_key = data["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = data["sub_url"].rsplit("/", 1)[1].split(".")[0]
    _wbi_keys.update(img_key=img_key, sub_key=sub_key, ts=now)
    return img_key, sub_key


async def wbi_query(params: dict) -> str:
    """签好名的 query string"""
    img_key, sub_key = await get_wbi_keys()
    return urllib.parse.urlencode(enc_wbi(params, img_key, sub_key))


async def get_ticket() -> str:
    now = time.time()
    if _ticket["ticket"] and now - _ticket["ts"] < _TICKET_TTL:
        return _ticket["ticket"]
    async with get_client_session().post(
        _TICKET_URL, params=ticket_params(int(now)), headers=HEADERS
    ) as resp:
        ticket = (await resp.json(content_type=None))["data"]["ticket"]
    _ticket.update(ticket=ticket, ts=now)
    return ticket
