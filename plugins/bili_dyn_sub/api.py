"""bili_dyn_sub 取数层：feed/space 请求、参数构造、错误码分流。

端点与 -352 语义借鉴 nonebot-bison (MIT, Copyright (c) 2021 felinae98) 的
platform/bilibili/platforms.py。踩过的坑：
- 未登录态必须带 dm_img 系列参数，缺了直接 -352；伪造近似空值即可，不签 wbi 也能返回。
- 风控/网络/解析失败一律抛异常，绝不返回空 items：把 352 的空结果当成正常 feed
  会用空集建基线，风控恢复后重复推送（bison 的老毛病）。
  `code==0 且 items 为空` 是合法结果，原样返回。
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
from typing import Any

import aiohttp
from nonebot import logger

from utils.network.http import get_client_session, get_effective_proxy
from utils.logging import get_exc_desc
from utils.text import truncate
from plugins.bili_dyn_sub.config import plugin_config
from plugins.bili_dyn_sub.credential import credential_manager

# ============================ 常量 ============================ #

FEED_SPACE_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
# UP 昵称：直播开放接口，无风控、无需 cookie
LIVE_USER_INFO_URL = "https://api.live.bilibili.com/live_user/v1/Master/info"

TIMEZONE_OFFSET = "-480"
PLATFORM = "web"
FEATURES = "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote"  # RSSHub 同款
WEB_LOCATION = "333.1387"

# dm_img 系列：未登录态风控必需，伪造近似空值即可
DM_IMG_LIST = "[]"
DM_IMG_INTER = '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}'
# dm_img_str / dm_cover_img_str 在 web 端是 WebGL 版本号与渲染器名的 base64 去尾 2 字符。
# 这里的渲染器名与 credential.py 指纹 payload 的 6bc5 字段保持一致，避免自相矛盾的指纹。
_FAKE_WEBGL_RENDERER = "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"
DM_COVER_IMG_STR = base64.b64encode(_FAKE_WEBGL_RENDERER.encode("utf-8")).decode(
    "ascii"
)[:-2]
# dm_img_str 取随机 2 字符（web 端为 base64 去尾后的短串，长度不参与校验）
_DM_IMG_STR_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# 网络层异常（连接失败/超时/读取中断），统一归为网络错误而非风控
_NET_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)
# 视为「暂时性服务端问题」的 HTTP 状态：按网络错误做短退避，不升级为风控
_TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


# ============================ 异常类型 ============================ #


class BiliApiError(Exception):
    """B 站取数失败的基类；直接抛出本类型表示其他错误码（含 -101）或响应结构异常"""

    def __init__(
        self,
        message: str = "",
        *,
        uid: str = "",
        code: int | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message: str = message
        self.uid: str = str(uid)
        self.code: int | None = code
        self.http_status: int | None = http_status
        super().__init__(message)

    def __str__(self) -> str:
        parts: list[str] = []
        if self.uid:
            parts.append(f"UID {self.uid}")
        if self.http_status is not None:
            parts.append(f"HTTP {self.http_status}")
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.message:
            parts.append(self.message)
        return " ".join(parts) or type(self).__name__


class BiliRiskControlError(BiliApiError):
    """code == -352：cookie/指纹层风控，强制重造 cookie 重试一次，仍失败就退避"""


class BiliIpBlockedError(BiliApiError):
    """HTTP 412：IP 层风控，换 cookie 无效，只能配 proxy"""


class BiliCaptchaError(BiliApiError):
    """code == 0 但 data 含 v_voucher：要 geetest 人机验证，放弃本轮"""


class BiliNetworkError(BiliApiError):
    """超时/连接失败/暂时性 5xx：网络问题而非风控，只做短退避"""


# ============================ 参数构造 ============================ #


def build_dm_params() -> dict[str, str]:
    """dm_img 系列风控参数：B 站只校验存在与格式，dm_img_str 每次随机，其余固定"""
    dm_img_str = "".join(random.choice(_DM_IMG_STR_ALPHABET) for _ in range(2))
    return {
        "dm_img_list": DM_IMG_LIST,
        "dm_img_str": dm_img_str,
        "dm_cover_img_str": DM_COVER_IMG_STR,
        "dm_img_inter": DM_IMG_INTER,
    }


def build_feed_params(uid: str) -> dict[str, str]:
    """feed/space 首页的完整查询参数"""
    params: dict[str, str] = {
        "host_mid": uid,
        "offset": "",
        "timezone_offset": TIMEZONE_OFFSET,
        "platform": PLATFORM,
        "features": FEATURES,
        "web_location": WEB_LOCATION,
    }
    params.update(build_dm_params())
    return params


# ============================ feed/space 取数 ============================ #


def _build_timeout() -> aiohttp.ClientTimeout:
    """bison 的 4s 超时在跨境时会误判为网络故障，这里按配置给（默认连接 5s / 总 10s）"""
    return aiohttp.ClientTimeout(
        total=plugin_config.http_timeout_total,
        connect=plugin_config.http_timeout_connect,
    )


async def fetch_space_feed(
    uid: str, *, force_refresh_cookie: bool = False
) -> dict[str, Any]:
    """拉取 UP 空间动态首页，返回响应的 data 段；失败抛 BiliApiError 的子类。

    force_refresh_cookie 用于 -352 后的重试：强制重造 cookie。
    """
    params = build_feed_params(uid)
    headers = credential_manager.build_headers(uid)
    cookies = await credential_manager.get_cookies(force_refresh=force_refresh_cookie)
    if not cookies:
        logger.debug(f"UID {uid} 本次取数没有可用 cookie，大概率会被 -352 拒绝")

    try:
        async with get_client_session("bilibili").get(
            FEED_SPACE_URL,
            params=params,
            headers=headers,
            cookies=cookies,
            proxy=get_effective_proxy(plugin_config.proxy),
            timeout=_build_timeout(),
        ) as resp:
            status = resp.status
            if status == 200:
                # 让 B 站自己给 buvid/ticket 续期（本插件不做主动续期）
                credential_manager.update_from_response(resp.cookies)
            body = await resp.text()
    except _NET_ERRORS as e:
        raise BiliNetworkError(
            f"请求 feed/space 失败: {get_exc_desc(e)}", uid=uid
        ) from e

    _check_http_status(uid, status, body)
    raw = _load_json(uid, status, body)
    return _dispatch_feed_payload(uid, raw)


def _check_http_status(uid: str, status: int, body: str) -> None:
    """HTTP 层分流：412 是 IP 风控，暂时性 5xx 归网络错误，其余非 200 归通用 API 错误"""
    if status == 200:
        return
    if status == 412:
        raise BiliIpBlockedError(
            "IP 被 B 站风控（换 cookie 无效，需配置 proxy）",
            uid=uid,
            http_status=status,
        )
    if status in _TRANSIENT_HTTP_STATUS:
        raise BiliNetworkError("B 站暂时性错误响应", uid=uid, http_status=status)
    raise BiliApiError(
        f"意外的 HTTP 状态，响应片段: {truncate(body, 200)}",
        uid=uid,
        http_status=status,
    )


def _load_json(uid: str, status: int, body: str) -> dict[str, Any]:
    """解析响应体；拿到 HTML 反爬页/截断内容时抛异常，绝不退化成空结果"""
    try:
        raw = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        raise BiliApiError(
            f"响应不是合法 JSON（{get_exc_desc(e)}），片段: {truncate(body, 200)}",
            uid=uid,
            http_status=status,
        ) from e
    if not isinstance(raw, dict):
        raise BiliApiError(
            f"响应顶层不是对象: {truncate(body, 200)}", uid=uid, http_status=status
        )
    return raw


def _dispatch_feed_payload(uid: str, raw: dict[str, Any]) -> dict[str, Any]:
    """按业务错误码分流，成功时返回 data 段"""
    code = raw.get("code")
    message = str(raw.get("message") or raw.get("msg") or "")

    if code == -352:
        raise BiliRiskControlError(f"触发风控: {message}", uid=uid, code=-352)
    if code != 0:
        raise BiliApiError(
            f"取数失败: {message}",
            uid=uid,
            code=code if isinstance(code, int) else None,
        )

    data = raw.get("data")
    if not isinstance(data, dict):
        raise BiliApiError(
            f"code=0 但 data 不是对象: {truncate(str(data), 200)}", uid=uid, code=0
        )
    # v_voucher 一般挂在 data 上，少数网关响应挂在顶层，两处都查
    voucher = data.get("v_voucher") or raw.get("v_voucher")
    if voucher:
        raise BiliCaptchaError(
            f"需要人机验证（v_voucher={truncate(str(voucher), 64)}），本轮放弃",
            uid=uid,
            code=0,
        )
    items = data.get("items")
    if items is not None and not isinstance(items, list):
        raise BiliApiError(
            f"data.items 类型异常: {type(items).__name__}", uid=uid, code=0
        )

    count = len(items) if isinstance(items, list) else 0
    logger.debug(f"UID {uid} 取数成功: {count} 条动态 has_more={data.get('has_more')}")
    return data


# ============================ UP 昵称 ============================ #


async def fetch_user_name(uid: str) -> str | None:
    """取 UP 昵称（订阅命令回显用）。走直播开放接口，无风控、无需 cookie；失败返回 None"""
    try:
        async with get_client_session("bilibili").get(
            LIVE_USER_INFO_URL,
            params={"uid": uid},
            headers=credential_manager.build_headers(),
            proxy=get_effective_proxy(plugin_config.proxy),
            timeout=_build_timeout(),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"查询 UID {uid} 昵称返回 HTTP {resp.status}")
                return None
            raw = await resp.json(content_type=None)
    except (*_NET_ERRORS, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"查询 UID {uid} 昵称失败: {get_exc_desc(e)}")
        return None

    if not isinstance(raw, dict) or raw.get("code") != 0:
        logger.warning(f"查询 UID {uid} 昵称失败: {truncate(str(raw), 200)}")
        return None
    data = raw.get("data")
    info = data.get("info") if isinstance(data, dict) else None
    uname = str(info.get("uname") or "").strip() if isinstance(info, dict) else ""
    if not uname:
        logger.warning(f"UID {uid} 的昵称为空，可能该 UID 不存在")
        return None
    return uname
