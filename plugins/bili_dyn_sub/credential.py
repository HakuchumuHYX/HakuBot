"""bili_dyn_sub cookie 三级降级管理（设计文档 §3.4）。

三级降级：
- L0 登录态：配置了 sessdata 时优先使用（官方文档背书，稳定性最好）；
- L1 纯 HTTP（默认）：finger/spi 取 buvid3/buvid4 → 构造指纹 payload + buvid_fp + _uuid
  → POST ExClimbWuzhi **激活** → GenWebTicket 取 bili_ticket。冷启动毫秒级，
  未激活的裸 buvid3 基本必吃 -352，所以激活这一步不能省；
- L2 Playwright 兜底：仅在 L1 失败 / 连续强制刷新仍风控时升级使用。

缓存策略：buvid3 记 1 年、bili_ticket 记 3 天，命中未过期缓存直接返回，不每轮重造；
force_refresh=True（api 层遇到 -352 时）丢弃缓存重造。**不做 cookie 自动续期** ——
AstrBot 因定时刷新 credential 触发上游异常而整体废弃该功能，这里坏了就重造。

登录态校验（verify_login / get_login_status）：**取数接口判不出登录态**。实测用伪造的
SESSDATA 打 feed/space 依然返回 code=0 并正常吐 13 条动态——失效登录态会被静默降级成
匿名行为，而不是报 -101。因此「sessdata 是否还有效」只能问 nav 接口
（GET /x/web-interface/nav，返回 data.isLogin / data.uname，匿名或失效 cookie 一律
code=-101 / isLogin=False；无风控、不需要签名，只要 UA + Referer）。校验结果缓存在内存并
可被上层查询，网络抖动不会被误判成掉登录（保留上一次已知结论）。
校验只影响「状态展示与告警」，不改变取数行为：配了 sessdata 就照样注入 SESSDATA 字段。

L2 的两个等待条件（wait_for_function('document.cookie.includes("bili_ticket")') +
wait_for_load_state("networkidle")）借鉴 nonebot-bison (MIT, Copyright (c) 2021 felinae98)
的 platform/bilibili/scheduler.py：只 goto + load 会拿到未激活的 buvid3，等于白跑浏览器。

GenWebTicket 的 HMAC-SHA256 算法与本仓 plugins/analysis_bilibili/sign.py 的
hmac_sha256/get_ticket 一致，此处按同法本地实现（避免 import 触发该插件包的注册副作用），
并改用本仓全局 session + 代理 + 超时配置。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

import aiohttp

from ..utils.json_io import atomic_write_json
from ..utils.network import get_client_session, get_effective_proxy
from ..utils.tools import get_exc_desc, get_logger, truncate
from .config import CREDENTIAL_FILE, plugin_config

logger = get_logger("bili_dyn_sub.credential")

# ============================ 常量 ============================ #

SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
EXCLIMB_URL = "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi"
TICKET_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"  # 登录态权威判据
SPACE_URL_TEMPLATE = "https://space.bilibili.com/{uid}/dynamic"
WWW_REFERER = "https://www.bilibili.com/"
SPACE_ORIGIN = "https://space.bilibili.com"

BUVID_TTL_SECONDS = 365 * 24 * 3600  # buvid3 官方 expires 约 1 年
TICKET_TTL_SECONDS = 3 * 24 * 3600  # bili_ticket 3 天
DEGRADED_TTL_SECONDS = 600.0  # 激活/ticket 缺失时的短有效期，10 分钟后再补一次
FAILED_RETRY_SECONDS = 300.0  # 三级全失败后的重试冷却，避免每轮都重造
MIN_REFRESH_INTERVAL_SECONDS = 60.0  # force_refresh 的最小间隔，避免风控风暴里反复重造
SAVE_THROTTLE_SECONDS = 300.0  # Set-Cookie 回写的落盘节流
FORCED_STREAK_RESET_SECONDS = 1800.0  # 距上次刷新超过此时长的强制刷新视为新一轮风控，重新从 L1 起
LOGIN_STATUS_TTL_SECONDS = 1800.0  # 登录态校验结果的缓存有效期（30 分钟）
LOGIN_ERROR_RETRY_SECONDS = 300.0  # 校验失败（网络抖动等）时的重试冷却，比正常 TTL 短

# GenWebTicket 的 web 端固定常量
_TICKET_HMAC_KEY = "XgwSnGZ1p"
_TICKET_KEY_ID = "ec02"

# 允许由响应 Set-Cookie 回写的字段（SESSDATA/bili_jct 只认配置，不接受回写）
_MUTABLE_COOKIE_KEYS = frozenset(
    {"buvid3", "buvid4", "b_nut", "buvid_fp", "_uuid", "b_lsid", "bili_ticket", "bili_ticket_expires", "sid"}
)

# 网络类异常（json.JSONDecodeError 是 ValueError 子类，一并归到这里）
_NET_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError)

# Playwright 超时（毫秒）
_BROWSER_GOTO_TIMEOUT_MS = 30_000
_BROWSER_WAIT_TIMEOUT_MS = 20_000

_MASK64 = 0xFFFFFFFFFFFFFFFF


# ============================ 纯函数（可单测） ============================ #


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
    parts = ["".join(random.choice(alphabet) for _ in range(size)) for size in (8, 4, 4, 4, 12)]
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


def hmac_sha256_hex(key: str, message: str) -> str:
    """HMAC-SHA256 十六进制摘要（与 plugins/analysis_bilibili/sign.py 的 hmac_sha256 同法）"""
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def build_ticket_params(ts: int) -> dict[str, str]:
    """构造 GenWebTicket 的查询参数（纯函数，便于单测其确定性部分）"""
    return {
        "key_id": _TICKET_KEY_ID,
        "hexsign": hmac_sha256_hex(_TICKET_HMAC_KEY, f"ts{ts}"),
        "context[ts]": str(ts),
        "csrf": "",
    }


def sessdata_fingerprint(sessdata: str) -> str:
    """sessdata 的短指纹，仅用于判断「配置是否换过」以作废登录态缓存。

    只取哈希前 12 位：既不落盘也不打日志原文，避免登录 cookie 出现在任何输出里。
    """
    text = (sessdata or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:12]


# ============================ 登录态 ============================ #


@dataclass(frozen=True, slots=True)
class LoginStatus:
    """一次登录态校验的结论快照（不可变，便于跨协程安全传递）。

    - configured: 是否配置了 sessdata（False 时不发请求，其余字段无意义）；
    - is_login:   nav 返回的 data.isLogin；校验失败时沿用上一次已知结论；
    - uname:      nav 返回的 data.uname，未登录为空串；
    - checked_at: 本次结论的产生时间（time.time()），也是 TTL 的计时起点；
    - error:      校验失败原因（网络/解析/未知业务码），成功为空串。
    """

    configured: bool
    is_login: bool
    uname: str = ""
    checked_at: float = 0.0
    error: str = ""

    @property
    def verified(self) -> bool:
        """本次结论是否来自一次成功的 nav 校验（而非缓存的降级/失败结论）"""
        return self.configured and not self.error

    @property
    def needs_reconfigure(self) -> bool:
        """是否**确定**需要人工重配 sessdata：配了、校验成功、但 isLogin=false。

        上层的「私聊超管提醒换 cookie」应以此为闸门：校验失败（error 非空）不算失效，
        否则一次网络抖动就会误报「登录态过期」。
        """
        return self.verified and not self.is_login

    def summary(self) -> str:
        """人类可读的一句话状态（供命令回显 / 告警文案复用）"""
        if not self.configured:
            return "未配置 sessdata（匿名取数）"
        if self.error:
            known = "有效" if self.is_login else "未确认"
            return f"登录态校验失败（{self.error}），沿用上次已知结论={known}"
        if self.is_login:
            return f"登录态有效（uname={self.uname or '-'}）"
        return "登录态已失效（nav 返回 isLogin=false），需重新配置 sessdata"


# ============================ CredentialManager ============================ #


class CredentialManager:
    """cookie 三级降级管理器；模块级单例见文件末尾的 credential_manager"""

    def __init__(self, cache_file: Path = CREDENTIAL_FILE) -> None:
        self._cache_file: Path = Path(cache_file)
        self._cookies: dict[str, str] = {}
        self._expire_at: dict[str, float] = {}
        self._source: str = ""
        self._user_agent: str = ""
        self._lock = asyncio.Lock()
        self._last_refresh_ts: float = 0.0
        self._last_save_ts: float = 0.0
        self._next_retry_ts: float = 0.0  # 全失败后的重试冷却截止
        self._forced_refresh_streak: int = 0  # 连续强制刷新次数，>1 时升级到 L2
        self._login_logged: bool = False
        self._save_task: Optional[asyncio.Task] = None
        # 登录态校验状态：结论快照 + 对应 sessdata 指纹（换了 sessdata 则缓存作废）+ 独立锁
        self._login_status: Optional[LoginStatus] = None
        self._login_status_fp: str = ""
        self._login_lock = asyncio.Lock()
        self._load()

    # -------------------- 持久化 --------------------

    def _load(self) -> None:
        """载入 cookie 缓存；文件缺失/损坏只记日志并以空状态启动"""
        if not self._cache_file.exists():
            logger.debug(f"cookie 缓存不存在，将在首次取数时生成: {self._cache_file}")
            return
        try:
            raw = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"读取 cookie 缓存失败，将重新生成: {get_exc_desc(e)}")
            return
        if not isinstance(raw, dict):
            logger.error("cookie 缓存顶层不是对象，将重新生成")
            return

        cookies = raw.get("cookies")
        if isinstance(cookies, dict):
            self._cookies = {str(k): str(v) for k, v in cookies.items() if k and v}
        expire_at = raw.get("expire_at")
        if isinstance(expire_at, dict):
            for key, value in expire_at.items():
                try:
                    self._expire_at[str(key)] = float(value)
                except (TypeError, ValueError):
                    logger.warning(f"cookie 缓存的 expire_at[{key}]={value!r} 非法，按已过期处理")
        self._source = str(raw.get("source") or "")
        self._user_agent = str(raw.get("user_agent") or "")
        logger.debug(f"载入 cookie 缓存: source={self._source or '-'} 字段={sorted(self._cookies)}")

    async def _save(self) -> None:
        """异步落盘（原子写放到线程里，不阻塞事件循环）。

        必须先拷贝再交给线程：直接传内部 dict，序列化期间事件循环回写 cookie 会
        导致线程里抛 "dictionary changed size during iteration"。
        """
        payload = {
            "cookies": dict(self._cookies),
            "expire_at": dict(self._expire_at),
            "source": self._source,
            "user_agent": self._user_agent,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            await asyncio.to_thread(atomic_write_json, self._cache_file, payload)
            self._last_save_ts = time.time()
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"写入 cookie 缓存失败，本次 cookie 仅存在内存中: {get_exc_desc(e)}")

    def _schedule_save(self) -> None:
        """节流落盘：窗口内的多次回写合并成一次，无事件循环时留给下次刷新落盘"""
        now = time.time()
        if now - self._last_save_ts < SAVE_THROTTLE_SECONDS:
            return
        self._last_save_ts = now  # 先占位，避免节流窗口内重复排任务
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("当前无事件循环，cookie 回写将在下次刷新时落盘")
            return
        self._save_task = loop.create_task(self._save())

    # -------------------- 缓存判定 --------------------

    def _is_cache_valid(self, now: float) -> bool:
        """缓存是否可用：有 buvid3、UA 未变、且所有记录的到期时间都还没到"""
        if not self._cookies.get("buvid3") or not self._expire_at:
            return False
        if self._user_agent and self._user_agent != plugin_config.user_agent:
            logger.info("配置的 UA 与生成 cookie 时不一致，丢弃缓存重造以避免指纹错配")
            return False
        return now < min(self._expire_at.values())

    def _compose(self) -> dict[str, str]:
        """输出给调用方的 cookie 副本；配置了 sessdata 时（L0）覆盖登录字段"""
        result = dict(self._cookies)
        sessdata = (plugin_config.sessdata or "").strip()
        if sessdata:
            result["SESSDATA"] = sessdata
            bili_jct = (plugin_config.bili_jct or "").strip()
            if bili_jct:
                result["bili_jct"] = bili_jct
            if not self._login_logged:
                logger.info("已配置 sessdata，使用登录态取数（L0），匿名 buvid/ticket 作为补充字段一并携带")
                self._login_logged = True
        return result

    # -------------------- 对外主入口 --------------------

    async def get_cookies(self, *, force_refresh: bool = False) -> dict[str, str]:
        """按三级降级返回可用 cookie dict。

        - 命中未过期缓存直接返回，不每轮重造；
        - force_refresh=True（api 层收到 -352 时）丢弃缓存重造，连续第 2 次起直接升级 L2；
        - 任一级失败都不抛给调用方：返回上一次可用缓存或空 dict，由 api/backoff 层决定退避。
        """
        async with self._lock:
            now = time.time()
            if force_refresh:
                if now - self._last_refresh_ts > FORCED_STREAK_RESET_SECONDS:
                    # 与上次刷新隔得够久，算作独立的风控事件，重新从 L1 起（否则计数只增不减会永远走 L2）
                    self._forced_refresh_streak = 0
                self._forced_refresh_streak += 1
                if self._cookies and now - self._last_refresh_ts < MIN_REFRESH_INTERVAL_SECONDS:
                    logger.debug(
                        f"距上次刷新不足 {int(MIN_REFRESH_INTERVAL_SECONDS)}s，本次沿用现有 cookie（避免风控风暴里反复重造）"
                    )
                    return self._compose()
            elif self._is_cache_valid(now):
                return self._compose()
            elif now < self._next_retry_ts:
                logger.debug("上次 cookie 生成失败仍在冷却窗口内，本轮沿用现有 cookie")
                return self._compose()

            await self._refresh_locked(escalate=force_refresh and self._forced_refresh_streak > 1)
            return self._compose()

    def build_headers(self, uid: str = "") -> dict[str, str]:
        """构造取数请求头（设计文档 §3.3 的三件套：浏览器 UA + Referer + Origin）"""
        uid = str(uid).strip()
        referer = SPACE_URL_TEMPLATE.format(uid=uid) if uid else WWW_REFERER
        return {
            "User-Agent": plugin_config.user_agent,
            "Referer": referer,
            "Origin": SPACE_ORIGIN if uid else WWW_REFERER.rstrip("/"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def mark_fetch_success(self) -> None:
        """由取数层在**业务成功（code==0）**后调用，清零「连续强制刷新」计数。

        注意不能把这个清零放在 update_from_response 里：-352 风控响应的 HTTP 状态同样是 200，
        也会带 Set-Cookie，若在那里清零则 _forced_refresh_streak 永远到不了 2，
        L2 Playwright 升级路径（get_cookies 的 escalate）会变成死代码。
        """
        self._forced_refresh_streak = 0

    def update_from_response(self, cookies: Mapping[str, Any]) -> None:
        """把响应 Set-Cookie 回写进持久 cookie（让 B 站自己续期；本插件不做主动续期）。

        兼容直接传 aiohttp 的 resp.cookies（值是 Morsel）或普通 {name: value} 字典。
        只做 cookie 续期，**不代表取数成功**（-352 也是 HTTP 200），成功语义见 mark_fetch_success。
        """
        now = time.time()
        changed: list[str] = []
        for key, value in cookies.items():
            name = str(key)
            # Morsel 的 str() 是 "name=value; Path=/" 整行，必须取 .value
            text = str(getattr(value, "value", value))
            if not text or name not in _MUTABLE_COOKIE_KEYS or self._cookies.get(name) == text:
                continue
            self._cookies[name] = text
            changed.append(name)
        if not changed:
            return
        if "bili_ticket" in changed:
            self._expire_at["bili_ticket"] = now + TICKET_TTL_SECONDS
        if "buvid3" in changed:
            self._expire_at["buvid3"] = now + BUVID_TTL_SECONDS
        logger.debug(f"由响应 Set-Cookie 续期字段: {changed}")
        self._schedule_save()

    # -------------------- 登录态校验 --------------------

    async def verify_login(self, *, force: bool = False) -> LoginStatus:
        """校验 sessdata 的真实登录态并缓存结论（GET /x/web-interface/nav）。

        为什么必须单独问 nav：取数接口 feed/space 对失效 SESSDATA **不报错** —— 实测伪造的
        SESSDATA 仍返回 code=0 且正常吐 13 条动态（静默退化成匿名行为），所以靠 -352/-101
        推断登录态是猜测，且对 sessdata 过期永远不会触发。nav 才是权威判据。

        - 未配置 sessdata：直接返回 configured=False 的结论，**不发任何请求**；
        - 命中缓存（LOGIN_STATUS_TTL_SECONDS 内、且 sessdata 未换过）直接返回，force=True 绕过；
        - 网络/解析/未知业务码：返回 error 非空的结论，并**保留上一次已知的 is_login**
          —— 网络抖动不该被误判成掉登录（校验失败按 LOGIN_ERROR_RETRY_SECONDS 尽快重试）。

        本方法只产出「状态」，不改变取数行为（cookie 组装见 _compose）。
        """
        sessdata = (plugin_config.sessdata or "").strip()
        if not sessdata:
            status = LoginStatus(configured=False, is_login=False, checked_at=time.time())
            self._login_status = status
            self._login_status_fp = ""
            return status

        fingerprint = sessdata_fingerprint(sessdata)
        async with self._login_lock:
            # 只有「同一份 sessdata 的上一次结论」才能作为缓存 / 失败时的兜底结论
            cached = self._login_status
            reusable = cached is not None and cached.configured and fingerprint == self._login_status_fp
            previous = cached if reusable else None
            if not force and previous is not None:
                ttl = LOGIN_ERROR_RETRY_SECONDS if previous.error else LOGIN_STATUS_TTL_SECONDS
                if time.time() - previous.checked_at < ttl:
                    return previous

            status = await self._fetch_login_status(previous)
            self._login_status = status
            self._login_status_fp = fingerprint
            self._log_login_transition(previous, status)
            return status

    def get_login_status(self) -> Optional[LoginStatus]:
        """返回最近一次登录态校验结论，未校验过则返回 None。**不发请求**，可随时调用"""
        return self._login_status

    async def _fetch_login_status(self, previous: Optional[LoginStatus]) -> LoginStatus:
        """真正请求 nav 并解析结论；失败时继承 previous 的 is_login/uname"""
        fallback_login = previous.is_login if previous is not None else False
        fallback_uname = previous.uname if previous is not None else ""
        raw, error = await self._request_raw("GET", NAV_URL, desc="nav 登录态校验", cookies=self._compose())
        now = time.time()
        if raw is None:
            return LoginStatus(
                configured=True,
                is_login=fallback_login,
                uname=fallback_uname,
                checked_at=now,
                error=error or "未知错误",
            )

        code = raw.get("code")
        if code not in (0, -101):
            # 限流（-509）等临时故障：不是「没登录」的证据，沿用上次结论并记为校验失败
            return LoginStatus(
                configured=True,
                is_login=fallback_login,
                uname=fallback_uname,
                checked_at=now,
                error=f"nav 返回 code={code} message={truncate(str(raw.get('message')), 60)}",
            )

        data = raw.get("data")
        data = data if isinstance(data, dict) else {}
        is_login = code == 0 and bool(data.get("isLogin"))
        uname = str(data.get("uname") or "").strip() if is_login else ""
        return LoginStatus(configured=True, is_login=is_login, uname=uname, checked_at=now)

    @staticmethod
    def _log_login_transition(previous: Optional[LoginStatus], current: LoginStatus) -> None:
        """只在状态跃迁时打 info/warning，稳态降为 debug（日志纪律见设计文档 §3.5）"""
        if current.error:
            logger.warning(f"B 站{current.summary()}")
            return
        if previous is not None and previous.verified and previous.is_login == current.is_login:
            logger.debug(f"B 站登录态校验：{current.summary()}")
            return
        if current.is_login:
            logger.info(f"B 站登录态校验通过：uname={current.uname or '-'}")
        else:
            logger.warning(
                "B 站 sessdata 已失效（nav 返回 isLogin=false）："
                "取数接口不会因此报错，只会静默退化为匿名请求（更易被风控），"
                "请重新配置 config.json 的 sessdata"
            )

    def _describe_login(self) -> str:
        """describe() 里附在 L0 后面的登录态简述"""
        status = self._login_status
        if status is None or not status.configured:
            return "(未校验)"
        if status.error:
            return f"(校验失败: {truncate(status.error, 40)})"
        if status.is_login:
            return f"(uname={status.uname or '-'})"
        return "(已失效)"

    def describe(self) -> str:
        """一行状态描述（供状态命令/排查日志使用）"""
        if (plugin_config.sessdata or "").strip():
            mode = f"L0 登录态{self._describe_login()}"
        elif self._source:
            mode = {"http": "L1 纯 HTTP", "browser": "L2 Playwright"}.get(self._source, self._source)
        else:
            mode = "未生成"
        if self._expire_at:
            earliest = datetime.fromtimestamp(min(self._expire_at.values())).isoformat(timespec="minutes")
        else:
            earliest = "-"
        return f"cookie 来源={mode} 缓存字段数={len(self._cookies)} 最早到期={earliest}"

    # -------------------- 刷新流程 --------------------

    async def _refresh_locked(self, *, escalate: bool = False) -> bool:
        """按级别顺序重造 cookie；escalate=True 时先走 L2（L1 已被证明救不回来）"""
        self._last_refresh_ts = time.time()
        levels = (
            (self._acquire_by_browser, self._acquire_by_http)
            if escalate
            else (self._acquire_by_http, self._acquire_by_browser)
        )
        for acquire in levels:
            result = await acquire()
            if result is None:
                continue
            cookies, degraded, source = result
            self._apply(cookies, degraded=degraded, source=source)
            self._clear_session_jar()
            await self._save()
            self._next_retry_ts = 0.0
            suffix = f"（指纹激活或 ticket 缺失，{int(DEGRADED_TTL_SECONDS)}s 后补一次）" if degraded else ""
            logger.info(f"已生成 B 站 cookie（{source}）: 字段={sorted(cookies)}{suffix}")
            return True

        self._next_retry_ts = time.time() + FAILED_RETRY_SECONDS
        if self._cookies:
            logger.warning(
                f"各级 cookie 生成均失败，沿用上一次缓存（可能已过期），{int(FAILED_RETRY_SECONDS)}s 后再试"
            )
        else:
            logger.error(
                f"各级 cookie 生成均失败且无历史缓存，本轮取数大概率 -352，{int(FAILED_RETRY_SECONDS)}s 后再试"
            )
        return False

    @staticmethod
    def _clear_session_jar() -> None:
        """清掉全局 session jar 里的 bilibili cookie。

        cookie 由调用方按请求显式传入，jar 里残留的上一代 buvid/ticket 会被一起发出去，
        新旧指纹混发反而更容易触发风控。
        """
        clear_domain = getattr(get_client_session().cookie_jar, "clear_domain", None)
        if clear_domain is None:
            logger.debug("当前 aiohttp 的 cookie jar 不支持 clear_domain，跳过清理")
            return
        try:
            clear_domain("bilibili.com")
        except (TypeError, ValueError) as e:
            logger.debug(f"清理 session jar 中的 bilibili cookie 失败: {get_exc_desc(e)}")

    def _apply(self, cookies: dict[str, str], *, degraded: bool, source: str) -> None:
        """替换当前 cookie 并按级别记 expire_at（降级结果只给短有效期）"""
        now = time.time()
        self._cookies = {str(k): str(v) for k, v in cookies.items() if k and v}
        expire_at = {
            "buvid3": now + BUVID_TTL_SECONDS,
            "bili_ticket": now + TICKET_TTL_SECONDS,
        }
        if degraded:
            expire_at = {key: min(value, now + DEGRADED_TTL_SECONDS) for key, value in expire_at.items()}
        self._expire_at = expire_at
        self._source = source
        self._user_agent = plugin_config.user_agent

    # -------------------- L1：纯 HTTP --------------------

    async def _acquire_by_http(self) -> Optional[tuple[dict[str, str], bool, str]]:
        """L1：spi 取 buvid → ExClimbWuzhi 激活 → GenWebTicket；失败返回 None"""
        spi = await self._request_json("GET", SPI_URL, desc="finger/spi")
        if spi is None:
            return None
        data = spi.get("data")
        data = data if isinstance(data, dict) else {}
        buvid3 = str(data.get("b_3") or "").strip()
        buvid4 = str(data.get("b_4") or "").strip()
        if not buvid3:
            logger.warning(f"finger/spi 未返回 b_3，L1 造 cookie 失败: {truncate(str(spi), 200)}")
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
        return {key: value for key, value in cookies.items() if value}, degraded, "http"

    async def _activate(self, payload: str, cookies: dict[str, str]) -> bool:
        """POST ExClimbWuzhi 激活 buvid；失败不致命（返回 False 走降级有效期）"""
        raw = await self._request_json(
            "POST", EXCLIMB_URL, desc="ExClimbWuzhi 指纹激活", json_body={"payload": payload}, cookies=cookies
        )
        if raw is None:
            logger.warning("buvid 指纹激活失败，未激活的 buvid3 大概率被 -352 拒绝，稍后重试")
            return False
        inner = raw.get("data")
        inner_code = inner.get("code") if isinstance(inner, dict) else None
        if inner_code not in (None, 0):
            logger.warning(f"buvid 指纹激活被拒绝: data.code={inner_code} msg={truncate(str(inner), 200)}")
            return False
        logger.debug("buvid 指纹激活成功")
        return True

    async def _fetch_ticket(self) -> Optional[tuple[str, int]]:
        """取 bili_ticket，返回 (ticket, 到期时间戳)；失败返回 None"""
        ts = int(time.time())
        raw = await self._request_json("POST", TICKET_URL, desc="GenWebTicket", params=build_ticket_params(ts))
        if raw is None:
            logger.warning("获取 bili_ticket 失败，缺少 ticket 的 cookie 更易被风控")
            return None
        data = raw.get("data")
        data = data if isinstance(data, dict) else {}
        ticket = str(data.get("ticket") or "").strip()
        if not ticket:
            logger.warning(f"GenWebTicket 响应里没有 ticket: {truncate(str(data), 200)}")
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
        params: Optional[dict[str, str]] = None,
        json_body: Optional[dict[str, Any]] = None,
        cookies: Optional[dict[str, str]] = None,
    ) -> Optional[dict[str, Any]]:
        """发一次造 cookie 相关请求并校验 code==0；任何异常都只记日志并返回 None"""
        raw, error = await self._request_raw(
            method, url, desc=desc, params=params, json_body=json_body, cookies=cookies
        )
        if raw is None:
            logger.warning(f"{desc} 请求失败: {error}")
            return None
        if raw.get("code") != 0:
            logger.warning(f"{desc} 返回 code={raw.get('code')} message={raw.get('message')!r}")
            return None
        return raw

    async def _request_raw(
        self,
        method: str,
        url: str,
        *,
        desc: str,
        params: Optional[dict[str, str]] = None,
        json_body: Optional[dict[str, Any]] = None,
        cookies: Optional[dict[str, str]] = None,
    ) -> tuple[Optional[dict[str, Any]], str]:
        """发一次请求并返回 (JSON 对象, 失败原因)，**不校验业务 code**。

        成功时失败原因为空串。登录态校验需要看到 -101 这样的业务码，所以不能复用
        _request_json（它把非 0 的 code 一律当失败吞掉），两者共用本方法的传输层。
        """
        headers = self.build_headers()
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        timeout = aiohttp.ClientTimeout(
            total=float(plugin_config.http_timeout_total),
            connect=float(plugin_config.http_timeout_connect),
        )
        try:
            async with get_client_session().request(
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

    # -------------------- L2：Playwright 兜底 --------------------

    async def _acquire_by_browser(self) -> Optional[tuple[dict[str, str], bool, str]]:
        """L2：真浏览器访问随机 UP 空间动态页，等 ticket + networkidle 后导出全部 cookie，
        再补一次 ExClimbWuzhi 激活（不补则实测稳定 -352，详见下方注释）"""
        if not plugin_config.enable_playwright_fallback:
            logger.debug("L2 Playwright 兜底已在配置中关闭，跳过")
            return None
        try:
            from playwright.async_api import Error as PlaywrightError

            from ..utils.browser import get_new_page
        except ImportError as e:
            logger.warning(f"Playwright 不可用，跳过 L2 兜底: {get_exc_desc(e)}")
            return None

        context_kwargs: dict[str, Any] = {"user_agent": plugin_config.user_agent, "locale": "zh-CN"}
        proxy = get_effective_proxy(plugin_config.proxy)
        if proxy:
            context_kwargs["proxy"] = {"server": proxy}
        url = SPACE_URL_TEMPLATE.format(uid=random.randint(1, 1000))
        logger.info(f"升级到 L2 Playwright 兜底造 cookie: {url}")
        try:
            async with get_new_page(device_scale_factor=1, **context_kwargs) as page:
                await page.goto(url, timeout=_BROWSER_GOTO_TIMEOUT_MS)
                await page.wait_for_load_state("load")
                # 以下两个等待条件照抄 bison：分别保证 GenWebTicket 与 ExClimbWuzhi 已完成，
                # 只 goto + load 会导出未激活的 buvid3，等于白跑浏览器
                await page.wait_for_function(
                    'document.cookie.includes("bili_ticket")', timeout=_BROWSER_WAIT_TIMEOUT_MS
                )
                await page.wait_for_load_state("networkidle", timeout=_BROWSER_WAIT_TIMEOUT_MS)
                raw_cookies = await page.context.cookies()
        except PlaywrightError as e:
            logger.warning(f"L2 Playwright 造 cookie 失败: {get_exc_desc(e)}")
            return None

        cookies: dict[str, str] = {}
        for item in raw_cookies:
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            if name and value:
                cookies[name] = value
        if not cookies.get("buvid3"):
            logger.warning("L2 导出的 cookie 中没有 buvid3，视为失败")
            return None
        # 浏览器导出的 cookie 里常缺 _uuid / b_lsid（B 站 web 端写 document.cookie 的时机与
        # 导出时点不一定重合），而 ExClimbWuzhi 的 payload 需要 _uuid。缺失才补，不覆盖浏览器给的值。
        for name, factory in (("_uuid", gen_uuid_infoc), ("b_lsid", gen_b_lsid)):
            if not cookies.get(name):
                cookies[name] = factory()
                logger.debug(f"L2 导出的 cookie 缺少 {name}，已本地补齐")

        # 关键修正（bison 缺这一步，也是它 -352 刷屏的原因之一）：
        # 只靠 bison 的两个等待条件（bili_ticket + networkidle）并不能保证 ExClimbWuzhi 真的跑过
        # ——随机 UID 的空间页可能根本不发这个请求。实测浏览器导出的 cookie 原样打 feed/space
        # 稳定 -352，补一次 ExClimbWuzhi 激活后同一批 cookie 立刻返回 code=0（13 条动态）。
        payload = build_activate_payload(uuid=cookies["_uuid"], user_agent=plugin_config.user_agent)
        activated = await self._activate(payload, cookies)
        if not activated:
            logger.warning("L2 导出的 cookie 补激活失败，可能仍会被 -352 拒绝")
        return cookies, (not activated) or not cookies.get("bili_ticket"), "browser"


# 模块级单例
credential_manager = CredentialManager()


async def verify_login(*, force: bool = False) -> LoginStatus:
    """校验当前配置的 sessdata 是否真的处于登录态（单例快捷入口）。

    详见 CredentialManager.verify_login：未配置 sessdata 不发请求，默认 30 分钟缓存，
    force=True 绕过缓存，网络异常保留上一次已知结论。
    """
    return await credential_manager.verify_login(force=force)


def get_login_status() -> Optional[LoginStatus]:
    """返回最近一次登录态校验结论（未校验过返回 None），不发请求（单例快捷入口）"""
    return credential_manager.get_login_status()
