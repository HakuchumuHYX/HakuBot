"""bili_dyn_sub cookie 与登录态。

匿名 cookie 用纯 HTTP 造：finger/spi 取 buvid3/buvid4 → 构造指纹 payload + buvid_fp + _uuid
→ POST ExClimbWuzhi 激活 → GenWebTicket 取 bili_ticket。未激活的裸 buvid3 基本必吃 -352，
激活这一步不能省。配了 sessdata 时在匿名字段之上叠加 SESSDATA。

缓存：buvid3 记 1 年、bili_ticket 记 3 天，到期或 -352 时重造；响应的 Set-Cookie 回写
匿名字段让 B 站自己续期，SESSDATA 只认配置、不接受回写。sessdata 不做自动刷新：
手动复制的 cookie 拿不到 refresh_token，刷新接口本身也是风控敏感操作。

登录态只能问 nav（/x/web-interface/nav 的 data.isLogin）：实测失效或伪造的 SESSDATA
打 feed/space 照样返回 code=0 和正常动态，静默退化成匿名请求，不会报 -101。
校验只影响状态展示和告警，不改变取数行为。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp
from nonebot import logger

from core.lifecycle import runtime
from utils.bilibili import ticket_params
from utils.json_io import atomic_write_json
from utils.network.http import get_client_session, get_effective_proxy
from utils.logging import get_exc_desc
from utils.text import truncate
from plugins.bili_dyn_sub.config import CREDENTIAL_FILE, plugin_config

# ============================ 常量 ============================ #

SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
EXCLIMB_URL = "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi"
TICKET_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
SPACE_URL_TEMPLATE = "https://space.bilibili.com/{uid}/dynamic"
WWW_REFERER = "https://www.bilibili.com/"
SPACE_ORIGIN = "https://space.bilibili.com"

BUVID_TTL_SECONDS = 365 * 24 * 3600  # buvid3 官方 expires 约 1 年
TICKET_TTL_SECONDS = 3 * 24 * 3600  # bili_ticket 3 天
DEGRADED_TTL_SECONDS = 600.0  # 激活/ticket 缺失时的短有效期，10 分钟后再补一次
FAILED_RETRY_SECONDS = 300.0  # 生成失败后的重试冷却，避免每轮都重造
MIN_REFRESH_INTERVAL_SECONDS = 60.0  # 强制刷新的最小间隔，避免风控风暴里反复重造
SAVE_THROTTLE_SECONDS = 300.0  # Set-Cookie 回写的落盘节流

# 允许由响应 Set-Cookie 回写的字段（SESSDATA 只认配置，不接受回写）
_MUTABLE_COOKIE_KEYS = frozenset(
    {
        "buvid3",
        "buvid4",
        "b_nut",
        "buvid_fp",
        "_uuid",
        "b_lsid",
        "bili_ticket",
        "bili_ticket_expires",
        "sid",
    }
)

# 网络类异常（json.JSONDecodeError 是 ValueError 子类，一并归到这里）
_NET_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)

_MASK64 = 0xFFFFFFFFFFFFFFFF


# ============================ 指纹 ============================ #


def _rotl64(value: int, bits: int) -> int:
    """64 位循环左移"""
    return ((value << bits) | (value >> (64 - bits))) & _MASK64


def _fmix64(value: int) -> int:
    """murmur3 的 64 位最终混淆"""
    value = (value ^ (value >> 33)) * 0xFF51AFD7ED558CCD & _MASK64
    value = (value ^ (value >> 33)) * 0xC4CEB9FE1A85EC53 & _MASK64
    return value ^ (value >> 33)


def murmur3_x64_128(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 x64_128 的纯 Python 实现（本仓无 mmh3 依赖），返回 128 位整数。

    buvid_fp 是浏览器对指纹 payload 做该哈希后的结果，值本身不参与鉴权计算，
    但必须是长度正确、与 payload 对应的 32 位十六进制串。
    """
    c1 = 0x87C37B91114253D5
    c2 = 0x4CF5AD432745937F
    h1 = h2 = seed & _MASK64
    length = len(data)
    blocks = length // 16

    for i in range(blocks):
        base = i * 16
        k1 = int.from_bytes(data[base : base + 8], "little")
        k2 = int.from_bytes(data[base + 8 : base + 16], "little")
        k1 = _rotl64(k1 * c1 & _MASK64, 31) * c2 & _MASK64
        h1 = (_rotl64(h1 ^ k1, 27) + h2) & _MASK64
        h1 = (h1 * 5 + 0x52DCE729) & _MASK64
        k2 = _rotl64(k2 * c2 & _MASK64, 33) * c1 & _MASK64
        h2 = (_rotl64(h2 ^ k2, 31) + h1) & _MASK64
        h2 = (h2 * 5 + 0x38495AB5) & _MASK64

    tail = data[blocks * 16 :]
    k1 = k2 = 0
    for i, byte in enumerate(tail):
        if i < 8:
            k1 |= byte << (8 * i)
        else:
            k2 |= byte << (8 * (i - 8))
    if len(tail) > 8:
        h2 ^= _rotl64(k2 * c2 & _MASK64, 33) * c1 & _MASK64
    if tail:
        h1 ^= _rotl64(k1 * c1 & _MASK64, 31) * c2 & _MASK64

    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & _MASK64
    h2 = (h2 + h1) & _MASK64
    h1 = _fmix64(h1)
    h2 = _fmix64(h2)
    h1 = (h1 + h2) & _MASK64
    h2 = (h2 + h1) & _MASK64
    return (h2 << 64) | h1


def gen_buvid_fp(payload: str, seed: int = 31) -> str:
    """由指纹 payload 计算 buvid_fp：murmur3_x64_128(payload, 31) 的低 64 位 + 高 64 位十六进制"""
    value = murmur3_x64_128(payload.encode("utf-8", "ignore"), seed)
    return f"{value & _MASK64:016x}{value >> 64:016x}"


def gen_uuid_infoc() -> str:
    """生成 _uuid：形如 8-4-4-4-12 的大写十六进制段 + 5 位毫秒尾数 + "infoc" """
    alphabet = "0123456789ABCDEF"
    parts = [
        "".join(random.choice(alphabet) for _ in range(size))
        for size in (8, 4, 4, 4, 12)
    ]
    tail = str(int(time.time() * 1000) % 100_000).ljust(5, "0")
    return f"{'-'.join(parts)}{tail}infoc"


def gen_b_lsid() -> str:
    """生成 b_lsid：8 位随机十六进制 + "_" + 当前毫秒时间戳的十六进制（大写）"""
    prefix = "".join(random.choice("0123456789ABCDEF") for _ in range(8))
    return f"{prefix}_{int(time.time() * 1000):X}"


def build_activate_payload(*, uuid: str, user_agent: str) -> str:
    """构造 ExClimbWuzhi 的指纹上报 payload（结构对齐浏览器上报的字段编号）。

    键名是 web 端混淆后的固定编号：3064 固定为 1、39c8 是埋点位、3c43 是浏览器环境内层字典、
    df35 放 _uuid。这里填的是一份合法且稳定的近似值——激活只要求 payload 结构成立，
    不要求与真实浏览器逐字节一致。
    """
    # fmt: off
    inner_env: dict[str, Any] = {
        "2673": 0, "5766": 24, "6527": 0, "7003": 1, "807e": 1,  # 5766=色深
        "b8ce": user_agent,  # UA，必须与轮询时一致，否则指纹错配
        "641c": 0, "07a4": "zh-CN", "1c57": "not available", "0bd0": 16,  # 0bd0=逻辑核心数
        "748e": [1920, 1080], "d61f": [1920, 1032],  # screen / avail screen
        "fc9d": -480, "6aa9": "Asia/Shanghai",  # 时区偏移与取数参数 timezone_offset 一致
        "75b8": 1, "3b21": 1, "8a1c": 0, "d52f": "not available", "adca": "Win32",
        "80c9": [], "13ab": "", "bfe9": "", "a3c1": [],  # 插件列表 / canvas / webgl / webgl 扩展
        "6bc5": "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ed31": 0, "72bd": 0, "097b": 0, "52cd": [0, 0, 0], "a658": [],  # a658=字体列表
        "d02f": "124.04347527516074",  # audio 指纹
    }
    content: dict[str, Any] = {
        "3064": 1, "5062": str(int(time.time() * 1000)), "03bf": WWW_REFERER,
        "39c8": "333.1387.fp.risk", "34f1": "", "d402": "", "654a": "", "6e7c": "1920x1080",
        "3c43": inner_env,
        "54ef": '{"in_new_ab":true,"ab_version":{},"ab_split_num":{}}',
        "8b94": "", "df35": uuid, "07a4": "zh-CN", "5f45": None, "db46": 0,
    }
    # fmt: on
    return json.dumps(content, separators=(",", ":"), ensure_ascii=False)


# ============================ 登录态 ============================ #


@dataclass(frozen=True, slots=True)
class LoginStatus:
    """一次 nav 校验的结论；校验失败时 error 非空，is_login/uname 沿用上一次结论"""

    is_login: bool
    uname: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.error:
            known = "有效" if self.is_login else "未确认"
            return f"校验失败（{self.error}），沿用上次结论：{known}"
        if self.is_login:
            return f"有效（uname={self.uname or '-'}）"
        return "已失效（nav 返回 isLogin=false），需重新配置 sessdata"


# ============================ CredentialManager ============================ #


class CredentialManager:
    def __init__(self) -> None:
        self._cookies: dict[str, str] = {}
        self._expire_at: dict[str, float] = {}
        self._user_agent: str = ""
        self._lock = asyncio.Lock()
        self._last_refresh_ts: float = 0.0
        self._last_save_ts: float = 0.0
        self._next_retry_ts: float = 0.0
        self._login_logged: bool = False
        self._save_task: asyncio.Task | None = None
        self._login_status: LoginStatus | None = None
        self._known_login: bool | None = None  # 最近一次校验成功时的结论，用来判断跃迁
        if CREDENTIAL_FILE.exists():
            raw = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
            self._cookies = raw["cookies"]
            self._expire_at = raw["expire_at"]
            self._user_agent = raw["user_agent"]

    # -------------------- 持久化 --------------------

    async def _save(self) -> None:
        # 先拷贝再交给线程，序列化期间回写 cookie 不会改到它
        payload = {
            "cookies": dict(self._cookies),
            "expire_at": dict(self._expire_at),
            "user_agent": self._user_agent,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            await asyncio.to_thread(atomic_write_json, CREDENTIAL_FILE, payload)
            self._last_save_ts = time.time()
        except OSError as e:
            logger.warning(
                f"写入 cookie 缓存失败，本次 cookie 仅存在内存中: {get_exc_desc(e)}"
            )

    def _schedule_save(self) -> None:
        """节流落盘：窗口内的多次回写合并成一次"""
        now = time.time()
        if now - self._last_save_ts < SAVE_THROTTLE_SECONDS:
            return
        self._last_save_ts = now  # 先占位，避免节流窗口内重复排任务
        self._save_task = runtime.spawn(
            self._save(), name="bilibili-credential-save", shutdown="drain"
        )

    async def flush(self) -> None:
        if self._save_task is not None and not self._save_task.done():
            await asyncio.shield(self._save_task)
        await self._save()

    # -------------------- cookie --------------------

    def _is_cache_valid(self, now: float) -> bool:
        """有 buvid3、UA 没换过、所有记录的到期时间都还没到"""
        if not self._cookies.get("buvid3") or not self._expire_at:
            return False
        if self._user_agent != plugin_config.user_agent:
            logger.info("配置的 UA 与生成 cookie 时不一致，丢弃缓存重造以避免指纹错配")
            return False
        return now < min(self._expire_at.values())

    def _compose(self) -> dict[str, str]:
        """输出给请求用的 cookie 副本；配了 sessdata 时叠加登录字段"""
        result = dict(self._cookies)
        sessdata = plugin_config.sessdata.strip()
        if sessdata:
            result["SESSDATA"] = sessdata
            if not self._login_logged:
                logger.info(
                    "已配置 sessdata，使用登录态取数，匿名 buvid/ticket 一并携带"
                )
                self._login_logged = True
        return result

    async def get_cookies(self, *, force_refresh: bool = False) -> dict[str, str]:
        """返回可用 cookie；生成失败不抛异常，沿用上一次的 cookie（可能为空）"""
        async with self._lock:
            now = time.time()
            if force_refresh:
                if (
                    self._cookies
                    and now - self._last_refresh_ts < MIN_REFRESH_INTERVAL_SECONDS
                ):
                    logger.debug("距上次刷新 cookie 不足 60s，本次沿用现有 cookie")
                    return self._compose()
            elif self._is_cache_valid(now) or now < self._next_retry_ts:
                return self._compose()
            await self._refresh_locked()
            return self._compose()

    def build_headers(self, uid: str = "") -> dict[str, str]:
        """请求头三件套：浏览器 UA + Referer + Origin（UA 必须与造 cookie 时一致）"""
        referer = SPACE_URL_TEMPLATE.format(uid=uid) if uid else WWW_REFERER
        return {
            "User-Agent": plugin_config.user_agent,
            "Referer": referer,
            "Origin": SPACE_ORIGIN if uid else WWW_REFERER.rstrip("/"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def update_from_response(self, cookies: Mapping[str, Any]) -> None:
        """把响应 Set-Cookie 回写进持久 cookie（只收白名单里的匿名字段）"""
        now = time.time()
        changed: list[str] = []
        for name, morsel in cookies.items():
            value = morsel.value
            if (
                not value
                or name not in _MUTABLE_COOKIE_KEYS
                or self._cookies.get(name) == value
            ):
                continue
            self._cookies[name] = value
            changed.append(name)
        if not changed:
            return
        if "bili_ticket" in changed:
            self._expire_at["bili_ticket"] = now + TICKET_TTL_SECONDS
        if "buvid3" in changed:
            self._expire_at["buvid3"] = now + BUVID_TTL_SECONDS
        logger.debug(f"由响应 Set-Cookie 续期字段: {changed}")
        self._schedule_save()

    async def _refresh_locked(self) -> None:
        self._last_refresh_ts = time.time()
        result = await self._acquire_by_http()
        if result is None:
            self._next_retry_ts = time.time() + FAILED_RETRY_SECONDS
            state = (
                "沿用上一次缓存（可能已过期）"
                if self._cookies
                else "且无历史缓存，本轮取数大概率 -352"
            )
            logger.warning(
                f"B 站 cookie 生成失败，{state}，{int(FAILED_RETRY_SECONDS)}s 后再试"
            )
            return

        cookies, degraded = result
        now = time.time()
        self._cookies = cookies
        # 激活或 ticket 缺失时只给短有效期，过一会儿再补
        ttl_cap = now + DEGRADED_TTL_SECONDS if degraded else float("inf")
        self._expire_at = {
            "buvid3": min(now + BUVID_TTL_SECONDS, ttl_cap),
            "bili_ticket": min(now + TICKET_TTL_SECONDS, ttl_cap),
        }
        self._user_agent = plugin_config.user_agent
        # cookie 按请求显式传入，session jar 里残留的上一代 buvid/ticket 会一起发出去，新旧指纹混发更易风控
        get_client_session("bilibili").cookie_jar.clear_domain("bilibili.com")
        await self._save()
        self._next_retry_ts = 0.0
        suffix = (
            f"（指纹激活或 ticket 缺失，{int(DEGRADED_TTL_SECONDS)}s 后补一次）"
            if degraded
            else ""
        )
        logger.info(f"已生成 B 站 cookie: 字段={sorted(cookies)}{suffix}")

    async def _acquire_by_http(self) -> tuple[dict[str, str], bool] | None:
        """spi 取 buvid → ExClimbWuzhi 激活 → GenWebTicket；返回 (cookie, 是否降级)，失败返回 None"""
        spi = await self._request_json("GET", SPI_URL, desc="finger/spi")
        if spi is None:
            return None
        data = spi.get("data")
        data = data if isinstance(data, dict) else {}
        buvid3 = str(data.get("b_3") or "").strip()
        buvid4 = str(data.get("b_4") or "").strip()
        if not buvid3:
            logger.warning(
                f"finger/spi 未返回 b_3，造 cookie 失败: {truncate(str(spi), 200)}"
            )
            return None

        uuid = gen_uuid_infoc()
        payload = build_activate_payload(uuid=uuid, user_agent=plugin_config.user_agent)
        cookies: dict[str, str] = {
            "buvid3": buvid3,
            "buvid4": buvid4,
            "b_nut": str(int(time.time())),
            "_uuid": uuid,
            "buvid_fp": gen_buvid_fp(payload),
            "b_lsid": gen_b_lsid(),
        }
        activated = await self._activate(payload, cookies)
        ticket = await self._fetch_ticket()
        if ticket is not None:
            cookies["bili_ticket"] = ticket[0]
            cookies["bili_ticket_expires"] = str(ticket[1])
        degraded = (not activated) or ticket is None
        return {key: value for key, value in cookies.items() if value}, degraded

    async def _activate(self, payload: str, cookies: dict[str, str]) -> bool:
        """POST ExClimbWuzhi 激活 buvid；失败不致命（返回 False 走降级有效期）"""
        raw = await self._request_json(
            "POST",
            EXCLIMB_URL,
            desc="ExClimbWuzhi 指纹激活",
            json_body={"payload": payload},
            cookies=cookies,
        )
        if raw is None:
            logger.warning(
                "buvid 指纹激活失败，未激活的 buvid3 大概率被 -352 拒绝，稍后重试"
            )
            return False
        inner = raw.get("data")
        inner_code = inner.get("code") if isinstance(inner, dict) else None
        if inner_code not in (None, 0):
            logger.warning(
                f"buvid 指纹激活被拒绝: data.code={inner_code} msg={truncate(str(inner), 200)}"
            )
            return False
        logger.debug("buvid 指纹激活成功")
        return True

    async def _fetch_ticket(self) -> tuple[str, int] | None:
        """取 bili_ticket，返回 (ticket, 到期时间戳)；失败返回 None"""
        ts = int(time.time())
        raw = await self._request_json(
            "POST", TICKET_URL, desc="GenWebTicket", params=ticket_params(ts)
        )
        if raw is None:
            logger.warning("获取 bili_ticket 失败，缺少 ticket 的 cookie 更易被风控")
            return None
        data = raw.get("data")
        data = data if isinstance(data, dict) else {}
        ticket = str(data.get("ticket") or "").strip()
        if not ticket:
            logger.warning(
                f"GenWebTicket 响应里没有 ticket: {truncate(str(data), 200)}"
            )
            return None
        try:
            created_at = int(data.get("created_at") or ts)
            ttl = int(data.get("ttl") or TICKET_TTL_SECONDS)
        except (TypeError, ValueError):
            logger.warning("GenWebTicket 的 created_at/ttl 非法，按默认 3 天有效期处理")
            created_at, ttl = ts, TICKET_TTL_SECONDS
        return ticket, created_at + ttl

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        desc: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """发一次造 cookie 相关请求并校验 code==0；任何异常都只记日志并返回 None"""
        raw, error = await self._request_raw(
            method, url, desc=desc, params=params, json_body=json_body, cookies=cookies
        )
        if raw is None:
            logger.warning(f"{desc} 请求失败: {error}")
            return None
        if raw.get("code") != 0:
            logger.warning(
                f"{desc} 返回 code={raw.get('code')} message={raw.get('message')!r}"
            )
            return None
        return raw

    async def _request_raw(
        self,
        method: str,
        url: str,
        *,
        desc: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """发一次请求并返回 (JSON 对象, 失败原因)，**不校验业务 code**。

        成功时失败原因为空串。登录态校验需要看到 -101 这样的业务码，所以不能复用
        _request_json（它把非 0 的 code 一律当失败吞掉），两者共用本方法的传输层。
        """
        headers = self.build_headers()
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        timeout = aiohttp.ClientTimeout(
            total=plugin_config.http_timeout_total,
            connect=plugin_config.http_timeout_connect,
        )
        try:
            async with get_client_session("bilibili").request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                cookies=cookies,
                proxy=get_effective_proxy(plugin_config.proxy),
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status}"
                raw = await resp.json(content_type=None)
        except _NET_ERRORS as e:
            return None, get_exc_desc(e)
        if not isinstance(raw, dict):
            return None, f"响应不是 JSON 对象: {truncate(str(raw), 200)}"
        return raw, ""

    # -------------------- 登录态 --------------------

    async def verify_login(self) -> bool:
        """问 nav 校验 sessdata（调用方保证已配置），返回是否刚从有效/未知变成失效。

        网络抖动、限流（-509 等）不算失效：记为校验失败并沿用上一次结论。
        """
        previous = self._login_status
        raw, error = await self._request_raw(
            "GET", NAV_URL, desc="nav 登录态校验", cookies=self._compose()
        )
        code = raw.get("code") if raw is not None else None
        if raw is None or code not in (0, -101):
            error = (
                error
                or f"nav 返回 code={code} message={truncate(str(raw.get('message')), 60)}"
            )
            self._login_status = LoginStatus(
                is_login=previous.is_login if previous else False,
                uname=previous.uname if previous else "",
                error=error,
            )
            logger.warning(f"B 站登录态校验失败，沿用上次结论: {error}")
            return False

        data = raw.get("data")
        data = data if isinstance(data, dict) else {}
        is_login = code == 0 and bool(data.get("isLogin"))
        uname = str(data.get("uname") or "").strip() if is_login else ""
        self._login_status = LoginStatus(is_login=is_login, uname=uname)
        known, self._known_login = self._known_login, is_login
        if known == is_login:
            logger.debug(f"B 站登录态校验：{self._login_status.summary()}")
            return False
        if is_login:
            logger.info(f"B 站登录态校验通过：uname={uname or '-'}")
            return False
        logger.warning(
            "B 站 sessdata 已失效（nav 返回 isLogin=false）：取数不会报错，"
            "只会静默退化为匿名请求（更易被风控），请重新配置 config.json 的 sessdata"
        )
        return True

    def describe(self) -> str:
        """订阅列表开头的两行状态：cookie 和登录态（只读内存，不发请求）"""
        if self._expire_at:
            earliest = datetime.fromtimestamp(min(self._expire_at.values())).isoformat(
                timespec="minutes"
            )
            cookie = f"已生成，最早到期 {earliest}"
        else:
            cookie = "未生成"
        if not plugin_config.sessdata.strip():
            login = "未配置 sessdata（匿名取数）"
        elif self._login_status is None:
            login = "尚未校验（启动后约 30s 完成首次校验）"
        else:
            login = self._login_status.summary()
        return f"🔑 cookie：{cookie}\n   登录态：{login}"


credential_manager = CredentialManager()
