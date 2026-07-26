"""bili_dyn_sub 取数层：feed/space 请求 + 参数构造 + 错误码分流（设计文档 §3.1/§3.2/§3.3/§3.5）。

端点与 -352 语义借鉴 nonebot-bison (MIT, Copyright (c) 2021 felinae98) 的
platform/bilibili/platforms.py（get_sub_list / get_target_name），并做了两处关键修正：

1. **参数集比 bison 全**：bison 只发 host_mid/timezone_offset/offset/features=itemOpusStyle
   四个参数，纯靠浏览器 cookie 硬撑，这是其风控频发的原因之一。本模块补齐 platform /
   web_location 与 dm_img 系列风控参数（社区结论：补上 dm_img 后即使不签 wbi 也能正常返回，
   缺失则 -352）。
2. **风控/网络/解析错误一律抛异常，绝不返回空 items**。bison 的故障模式①（重启 + 首拉 352
   → 用空集建基线 → 风控恢复后重复推送）根因就是"把 352 的空结果当成正常 feed"。本模块从
   结构上消灭这一类 bug：本层只有两种结局 —— 返回 code==0 的真实 data，或者抛出带类型的异常。
   `code==0 且 items==[]` 是合法结果（该 UP 确实没动态），必须原样返回，不能当错误。

调用方（scheduler）按异常类型分流，退避与日志纪律见 backoff.py。
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import random
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import aiohttp

from ..utils.network import get_client_session, get_effective_proxy
from ..utils.tools import get_exc_desc, get_logger, truncate
from .config import plugin_config
from .credential import credential_manager

logger = get_logger("bili_dyn_sub.api")

# ============================ 常量 ============================ #

# 空间动态列表（bison / bilichat-request / RSSHub / bilibili-api 一致采用此端点，不用 gRPC）
FEED_SPACE_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
# UP 昵称：直播开放接口，无风控、无需 cookie
LIVE_USER_INFO_URL = "https://api.live.bilibili.com/live_user/v1/Master/info"

# 固定取数参数（§3.2）
TIMEZONE_OFFSET = "-480"
PLATFORM = "web"
FEATURES = "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote"  # RSSHub 同款
WEB_LOCATION = "333.1387"

# dm_img 系列：未登录态风控必需，伪造近似空值即可
DM_IMG_LIST = "[]"
DM_IMG_INTER = '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}'
# dm_img_str / dm_cover_img_str 在 web 端是 WebGL 版本号与渲染器名的 base64 去尾 2 字符。
# 这里的渲染器名与 credential.py 指纹 payload 的 6bc5 字段保持一致，避免自相矛盾的指纹。
_FAKE_WEBGL_RENDERER = (
    "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"
)
DM_COVER_IMG_STR = base64.b64encode(_FAKE_WEBGL_RENDERER.encode("utf-8")).decode("ascii")[:-2]
# dm_img_str 取随机 2 字符（web 端为 base64 去尾后的短串，长度不参与校验）
_DM_IMG_STR_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# 网络层异常（连接失败/超时/读取中断），统一归为网络错误而非风控
_NET_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)
# 视为「暂时性服务端问题」的 HTTP 状态：按网络错误做短退避，不升级为风控
_TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


# ============================ 异常类型（§3.5） ============================ #


class BiliApiError(Exception):
    """B 站取数失败的基类；直接抛出本类型表示「其他未分类错误码」。

    scheduler 可用 `except BiliApiError` 兜住本模块的全部失败，再按子类分流。
    """

    def __init__(
        self,
        message: str = "",
        *,
        uid: str = "",
        code: Optional[int] = None,
        http_status: Optional[int] = None,
    ) -> None:
        self.message: str = message
        self.uid: str = str(uid)
        self.code: Optional[int] = code
        self.http_status: Optional[int] = http_status
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
    """code == -352：账号/指纹层风控。前 1-2 次强制刷新 cookie 重试，仍失败进 per-UID 指数退避"""


class BiliIpBlockedError(BiliApiError):
    """HTTP 412：IP 层风控（机房 IP 高发）。换 cookie 无效，唯一解是 proxy，退避远长于 352"""


class BiliCaptchaError(BiliApiError):
    """code == 0 但 data 含 v_voucher：需 geetest 人机验证。放弃本轮 + warn，不尝试自动过验证码"""


class BiliAuthError(BiliApiError):
    """code == -101：登录态失效（配置了 sessdata 时）。标记失效并提示重新配置，不原地重试"""


class BiliSignError(BiliApiError):
    """code == -403：wbi/签名问题（仅在 enable_wbi 时可能出现）。刷新 wbi key 后重试"""


class BiliNetworkError(BiliApiError):
    """超时/连接失败/暂时性 5xx：网络问题而非风控，只做短退避"""


# ============================ 参数构造（纯函数，可单测） ============================ #


def build_dm_params() -> dict[str, str]:
    """构造 dm_img 系列风控参数（§3.2）。

    未登录态缺这四个参数会直接 -352；B 站只校验其存在与格式，不校验内容真实性，
    因此伪造近似空值即可。dm_img_str 每次随机，其余为固定伪造值。
    """
    dm_img_str = "".join(random.choice(_DM_IMG_STR_ALPHABET) for _ in range(2))
    return {
        "dm_img_list": DM_IMG_LIST,
        "dm_img_str": dm_img_str,
        "dm_cover_img_str": DM_COVER_IMG_STR,
        "dm_img_inter": DM_IMG_INTER,
    }


def build_feed_params(uid: str, *, offset: str = "") -> dict[str, str]:
    """构造 feed/space 的完整查询参数（§3.2 表格，全部为字符串以便直接给 aiohttp）"""
    params: dict[str, str] = {
        "host_mid": str(uid).strip(),
        "offset": offset,  # 首页为空串
        "timezone_offset": TIMEZONE_OFFSET,
        "platform": PLATFORM,
        "features": FEATURES,
        "web_location": WEB_LOCATION,
    }
    params.update(build_dm_params())
    return params


_SIGN_MODULE_PATH = Path(__file__).resolve().parent.parent / "analysis_bilibili" / "sign.py"
_sign_module: Optional[ModuleType] = None


def _load_sign_module() -> Optional[ModuleType]:
    """按文件路径单独加载 plugins/analysis_bilibili/sign.py（复用其 wbi 实现），失败返回 None。

    不写 `from ..analysis_bilibili.sign import ...`：那会连带执行该插件包的 __init__.py
    （on_regex 注册 + require("nonebot_plugin_saa") + get_driver().config），等于把另一个插件的
    注册副作用拖进取数路径，在非 NoneBot 上下文（单测/命令行）里会直接抛 ValueError。
    sign.py 自身只依赖 stdlib + aiohttp，按路径加载即可安全复用；代价是本模块持有一份独立的
    wbi key 缓存（启用 wbi 时每小时最多多一次 nav 请求）。
    """
    global _sign_module
    if _sign_module is not None:
        return _sign_module
    spec = importlib.util.spec_from_file_location("bili_dyn_sub_wbi_sign", _SIGN_MODULE_PATH)
    if spec is None or spec.loader is None:
        logger.warning(f"无法定位 wbi 签名模块 {_SIGN_MODULE_PATH}，本次不签名")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError, ValueError) as e:
        logger.warning(f"加载 wbi 签名模块失败，本次不签名: {get_exc_desc(e)}")
        return None
    _sign_module = module
    return module


async def _sign_with_wbi(params: dict[str, str]) -> dict[str, str]:
    """给参数补 wbi 签名（wts + w_rid），复用 plugins/analysis_bilibili/sign.py 的实现。

    第一版 enable_wbi 默认关闭（bison/bilichat/RSSHub 都没签仍在跑），仅在大面积 352 时启用。
    取 key 失败时退回未签名参数：签名缺失最多得到 -403，比整轮不取数更好。
    """
    sign = _load_sign_module()
    if sign is None:
        return params
    try:
        img_key, sub_key = await sign.getWbiKeys()
    except (*_NET_ERRORS, KeyError, TypeError, ValueError) as e:
        logger.warning(f"获取 wbi key 失败，本次不签名: {get_exc_desc(e)}")
        return params
    signed = sign.encWbi(dict(params), img_key, sub_key)
    return {str(key): str(value) for key, value in signed.items()}


def invalidate_wbi_keys() -> None:
    """作废 wbi key 缓存，供 scheduler 收到 -403 后重试前调用（§3.5）"""
    sign = _load_sign_module()
    if sign is None:
        return
    cache = getattr(sign, "_wbi_keys_cache", None)
    if not isinstance(cache, dict):
        logger.debug("wbi 签名模块没有可作废的 key 缓存，跳过")
        return
    cache["ts"] = 0.0
    logger.info("已作废 wbi key 缓存，下次取数将重新获取")


# ============================ feed/space 取数 ============================ #


def _build_timeout() -> aiohttp.ClientTimeout:
    """按配置构造超时（连接 5s / 总 10s；bison 的 4s 在跨境时会误判为网络故障）"""
    return aiohttp.ClientTimeout(
        total=max(1.0, float(plugin_config.http_timeout_total)),
        connect=max(1.0, float(plugin_config.http_timeout_connect)),
    )


async def fetch_space_feed(
    uid: str,
    *,
    force_refresh_cookie: bool = False,
    offset: str = "",
) -> dict[str, Any]:
    """拉取指定 UP 的空间动态列表，返回响应的 data 部分（含 items/offset/has_more）。

    - `force_refresh_cookie=True`：收到 -352 后重试本轮时使用，强制重造 cookie；
    - `offset`：分页游标，第一版只取首页（空串），保留参数以便将来翻页；
    - **只有 code==0 且结构合法才返回**，其余一切情况抛 BiliApiError 的子类，
      调用方绝不会因为风控/网络/解析失败而看到"空 items"（§3.5）。
      注意 `code==0 且 items==[]` 是合法结果（该 UP 确实没有动态），会正常返回。
    """
    uid = str(uid).strip()
    params = build_feed_params(uid, offset=offset)
    if plugin_config.enable_wbi:
        params = await _sign_with_wbi(params)

    headers = credential_manager.build_headers(uid)
    cookies = await credential_manager.get_cookies(force_refresh=force_refresh_cookie)
    if not cookies:
        logger.debug(f"UID {uid} 本次取数没有可用 cookie，大概率会被 -352 拒绝")

    try:
        async with get_client_session().get(
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
        raise BiliNetworkError(f"请求 feed/space 失败: {get_exc_desc(e)}", uid=uid) from e

    _check_http_status(uid, status, body)
    raw = _load_json(uid, status, body)
    data = _dispatch_feed_payload(uid, raw)
    # 只有走到这里才算业务成功：清零「连续强制刷新」计数（-352 也是 HTTP 200，不能靠状态码判断）
    credential_manager.mark_fetch_success()
    return data


def _check_http_status(uid: str, status: int, body: str) -> None:
    """HTTP 层分流：412 是 IP 风控，暂时性 5xx 归网络错误，其余非 200 归通用 API 错误"""
    if status == 200:
        return
    if status == 412:
        raise BiliIpBlockedError(
            "IP 被 B 站风控（换 cookie 无效，需配置 proxy）", uid=uid, http_status=status
        )
    if status in _TRANSIENT_HTTP_STATUS:
        raise BiliNetworkError("B 站暂时性错误响应", uid=uid, http_status=status)
    raise BiliApiError(f"意外的 HTTP 状态，响应片段: {truncate(body, 200)}", uid=uid, http_status=status)


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
        raise BiliApiError(f"响应顶层不是对象: {truncate(body, 200)}", uid=uid, http_status=status)
    return raw


def _dispatch_feed_payload(uid: str, raw: dict[str, Any]) -> dict[str, Any]:
    """按业务错误码分流（§3.5 表），成功时返回 data 段"""
    code = raw.get("code")
    message = str(raw.get("message") or raw.get("msg") or "")

    if code == -352:
        raise BiliRiskControlError(f"触发风控: {message}", uid=uid, code=-352)
    if code == -101:
        raise BiliAuthError(f"登录态失效，请重新配置 sessdata: {message}", uid=uid, code=-101)
    if code == -403:
        raise BiliSignError(f"签名/权限校验失败（wbi）: {message}", uid=uid, code=-403)
    if code != 0:
        raise BiliApiError(f"取数失败: {message}", uid=uid, code=code if isinstance(code, int) else None)

    data = raw.get("data")
    if not isinstance(data, dict):
        raise BiliApiError(
            f"code=0 但 data 不是对象: {truncate(str(data), 200)}", uid=uid, code=0
        )
    # v_voucher 一般挂在 data 上，少数网关响应挂在顶层，两处都查
    voucher = data.get("v_voucher") or raw.get("v_voucher")
    if voucher:
        raise BiliCaptchaError(
            f"需要人机验证（v_voucher={truncate(str(voucher), 64)}），本轮放弃", uid=uid, code=0
        )
    items = data.get("items")
    if items is not None and not isinstance(items, list):
        raise BiliApiError(f"data.items 类型异常: {type(items).__name__}", uid=uid, code=0)

    count = len(items) if isinstance(items, list) else 0
    logger.debug(f"UID {uid} 取数成功: {count} 条动态 has_more={data.get('has_more')}")
    return data


# ============================ UP 昵称 ============================ #


async def fetch_user_name(uid: str) -> Optional[str]:
    """取 UP 昵称（订阅命令回显用）。走直播开放接口，无风控、无需 cookie；失败返回 None"""
    uid = str(uid).strip()
    try:
        async with get_client_session().get(
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
