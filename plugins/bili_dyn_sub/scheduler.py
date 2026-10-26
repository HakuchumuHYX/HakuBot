"""bili_dyn_sub 定时任务：轮询、筛选、发送、退避、登录态校验。

每轮按 UID 串行错开取数，同一 UP 被多个群订阅只拉一次再分发。
风控、网络、解析失败一律不动游标；只有 code==0 才推进状态。
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

from nonebot import get_bot, get_driver, logger, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.exception import ActionFailed, NetworkError

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler as apscheduler

from core.lifecycle import on_plugin_shutdown, on_plugin_startup, runtime
from utils.logging import get_exc_desc
from utils.onebot.forward import send_forward_msg
from plugins.bili_dyn_sub import api
from plugins.bili_dyn_sub.config import plugin_config
from plugins.bili_dyn_sub.credential import credential_manager
from plugins.bili_dyn_sub.parser import ParsedDynamic, parse_feed, should_skip
from plugins.bili_dyn_sub.render import build_messages
from plugins.bili_dyn_sub.store import store

LOGIN_CHECK_INTERVAL_HOURS = 6
LOGIN_CHECK_STARTUP_DELAY_SECONDS = 30.0  # 等适配器把 Bot 连上，告警才发得出去

RISK_BACKOFF_BASE_SECONDS = 300.0
RISK_BACKOFF_MAX_SECONDS = 3600.0
IP_BLOCK_BACKOFF_BASE_SECONDS = 1800.0  # 412 换 cookie 无效，退避远长于 352
IP_BLOCK_BACKOFF_MAX_SECONDS = 14400.0
NETWORK_BACKOFF_SECONDS = 60.0

# 基线已建立后连续这么多轮空 feed，提示疑似软风控（code=0 但 items 为空，不报错也不退避）
EMPTY_FEED_WARN_THRESHOLD = 5


# ---------------------------------------------------------------- 退避


class Backoff:
    """-352 是 cookie 层面的，412 是 IP 层面的，网络错误也是整机的，所以全局只有一份：
    退避中整轮跳过，任何一个 UID 取数成功就清零。"""

    def __init__(self) -> None:
        self.until = 0.0
        self.risk_count = 0  # 本段失败里 352/412 的次数，决定退避倍数
        self.cookie_refreshed = False  # 本段失败里是否已强制重造过 cookie

    def remaining(self) -> int:
        return max(0, math.ceil(self.until - time.time()))

    def escalate(self, base: float, cap: float) -> int:
        self.risk_count += 1
        return int(min(base * 2 ** (self.risk_count - 1), cap))

    def hold(self, delay: float) -> None:
        self.until = max(self.until, time.time() + delay)

    def reset(self) -> None:
        self.risk_count = 0
        self.cookie_refreshed = False


backoff = Backoff()
_last_error: dict[str, str] = {}  # uid → 当前错误类型；只在进入或类型变化时记 warning
_empty_feed_streak: dict[str, int] = {}


def _fail(uid: str, key: str, delay: float, message: str) -> None:
    if delay:
        backoff.hold(delay)
    if _last_error.get(uid) == key:
        logger.debug(message)
    else:
        logger.warning(message)
    _last_error[uid] = key


def _succeed(uid: str) -> None:
    backoff.reset()
    key = _last_error.pop(uid, None)
    if key:
        logger.info(f"UID {uid} 取数恢复正常（此前错误：{key}）")


async def _fetch_feed(uid: str) -> dict[str, Any] | None:
    """取一次 feed；成功返回 data 段，失败按类型退避并返回 None"""
    try:
        try:
            return await api.fetch_space_feed(uid)
        except api.BiliRiskControlError:
            if backoff.cookie_refreshed:
                raise
            backoff.cookie_refreshed = True
            logger.info(f"UID {uid} 触发 -352 风控，强制重造 cookie 后重试一次")
            return await api.fetch_space_feed(uid, force_refresh_cookie=True)
    except api.BiliRiskControlError as e:
        delay = backoff.escalate(RISK_BACKOFF_BASE_SECONDS, RISK_BACKOFF_MAX_SECONDS)
        _fail(
            uid,
            "-352",
            delay,
            f"UID {uid} 重造 cookie 后仍被 -352 风控（{e}），退避 {delay}s；"
            "长期不恢复请检查 sessdata 是否有效",
        )
    except api.BiliIpBlockedError as e:
        delay = backoff.escalate(
            IP_BLOCK_BACKOFF_BASE_SECONDS, IP_BLOCK_BACKOFF_MAX_SECONDS
        )
        _fail(
            uid,
            "412",
            delay,
            f"UID {uid} 命中 IP 层风控（{e}），换 cookie 无效，退避 {delay}s；"
            "请在 config.json 配置 proxy",
        )
    except api.BiliNetworkError as e:
        _fail(
            uid,
            "network",
            NETWORK_BACKOFF_SECONDS,
            f"UID {uid} 取数网络异常（{e}），退避 {int(NETWORK_BACKOFF_SECONDS)}s",
        )
    except api.BiliApiError as e:
        # 其他错误码（含 -101、人机验证）/ 响应结构异常：不退避，本轮放弃
        _fail(uid, f"api:{e.code}", 0, f"UID {uid} 取数失败（{e}），本轮不推进状态")
    return None


# ---------------------------------------------------------------- 筛选


class PushSelection(NamedTuple):
    """两个抑制计数对外语义不同：
    - overflow：超出单轮条数上限的，向群里提示“另有 X 条未展示”，否则像漏推；
    - stale：超出推送窗口的停机积压，只记日志——默认窗口的意义就是重启对群友无感。
    """

    to_push: list[ParsedDynamic]
    overflow: int
    stale: int


def _select_pushable(uid: str, fresh: list[ParsedDynamic]) -> PushSelection:
    """从未读动态（升序）里筛出要推的；跳过类型不计入两个抑制计数"""
    candidates: list[ParsedDynamic] = []
    for parsed in fresh:
        if should_skip(parsed):
            logger.debug(
                f"UID {uid} 动态 {parsed.dyn_id} 类型 {parsed.dyn_type} 跳过推送"
            )
        else:
            candidates.append(parsed)

    stale = 0
    age_limit_seconds = plugin_config.max_dynamic_age_minutes * 60
    if age_limit_seconds > 0:
        deadline = time.time() - age_limit_seconds
        kept: list[ParsedDynamic] = []
        for parsed in candidates:
            if parsed.pub_ts and parsed.pub_ts < deadline:
                logger.info(
                    f"UID {uid} 动态 {parsed.dyn_id} 发布已超 {age_limit_seconds // 60}min，"
                    "超出推送窗口，仅标记已读"
                )
                stale += 1
            else:
                kept.append(parsed)
        candidates = kept

    overflow = 0
    max_count = plugin_config.max_push_per_round
    if max_count > 0 and len(candidates) > max_count:
        overflow = len(candidates) - max_count
        candidates = candidates[-max_count:]
        logger.info(
            f"UID {uid} 本轮新动态超过 {max_count} 条上限，较旧的 {overflow} 条仅标记已读"
        )

    return PushSelection(candidates, overflow, stale)


def _track_empty_feed(uid: str, parsed_list: list[ParsedDynamic]) -> None:
    """code==0 且一条动态都没有，可能是 UP 真没动态，也可能是软风控（新造的 cookie 尤其常见）"""
    streak = _empty_feed_streak.pop(uid, 0)
    if parsed_list:
        if streak >= EMPTY_FEED_WARN_THRESHOLD:
            logger.info(f"UID {uid} 连续 {streak} 轮空 feed 后恢复正常")
        return
    streak += 1
    _empty_feed_streak[uid] = streak
    if streak == EMPTY_FEED_WARN_THRESHOLD and store.has_baseline(uid):
        logger.warning(
            f"UID {uid} 已连续 {streak} 轮取数成功但 feed 为空，疑似软风控；"
            "cookie 会随时间自愈，长期不恢复请检查 sessdata"
        )


# ---------------------------------------------------------------- 发送


@dataclass(frozen=True, slots=True)
class SendTarget:
    """群聊或私聊二选一；`b站订阅测试` 用私聊目标预览，和真实推送走同一套发送逻辑"""

    group_id: int | None = None
    user_id: int | None = None

    def __str__(self) -> str:
        return (
            f"私聊 {self.user_id}"
            if self.user_id is not None
            else f"群 {self.group_id}"
        )


async def _send_with_retry(
    bot: Bot, target: SendTarget, payload: Message, *, desc: str
) -> bool:
    """发一条消息，失败重试；每次发送后都间隔 send_interval_seconds，避免连发触发风控。

    超时属于“可能已送达”，不重试，避免重复推送。
    """
    attempts = plugin_config.send_retry_times
    interval = plugin_config.send_interval_seconds

    for attempt in range(1, attempts + 1):
        try:
            if target.user_id is not None:
                await bot.send_private_msg(user_id=target.user_id, message=payload)
            else:
                await bot.send_group_msg(group_id=target.group_id, message=payload)
        except NetworkError as e:
            await asyncio.sleep(interval)
            if "timeout" in str(e).lower():
                logger.warning(
                    f"{target} 发送{desc}超时，服务端可能已处理，跳过重试避免重复推送"
                )
                return True
            logger.info(
                f"{target} 发送{desc}网络错误（第 {attempt}/{attempts} 次）: {get_exc_desc(e)}"
            )
        except ActionFailed as e:
            await asyncio.sleep(interval)
            logger.info(
                f"{target} 发送{desc}失败（第 {attempt}/{attempts} 次）: {get_exc_desc(e)}"
            )
        else:
            await asyncio.sleep(interval)
            return True

    logger.error(f"{target} 发送{desc}最终失败，已重试 {attempts} 次")
    return False


async def dispatch_segments(
    bot: Bot, target: SendTarget, segments: list[MessageSegment]
) -> None:
    """照 bison 的拆包：首段（文字卡片）单发；余下 1 段单发，≥2 段合并转发"""
    await _send_with_retry(bot, target, Message(segments[0]), desc="动态文字卡片")

    rest = segments[1:]
    if not rest:
        return
    if len(rest) == 1:
        await _send_with_retry(bot, target, Message(rest[0]), desc="动态配图")
        return

    await send_forward_msg(
        bot,
        group_id=target.group_id,
        user_id=target.user_id,
        items=[Message(segment) for segment in rest],
        fallback_interval=plugin_config.send_interval_seconds,
    )


# ---------------------------------------------------------------- 轮询


async def _poll_uid(bot: Bot, uid: str) -> None:
    data = await _fetch_feed(uid)
    if data is None:
        return
    _succeed(uid)
    targets = store.groups(uid)
    if not targets:
        # 取数期间被退订：退订已删掉游标，这里不能再写回去
        return
    store.touch_success(uid)
    parsed_list = parse_feed(data)
    _track_empty_feed(uid, parsed_list)

    if not store.has_baseline(uid):
        # 空 feed 可能是软风控，用它建基线会在恢复后把整页历史当新动态
        if not parsed_list:
            logger.info(f"UID {uid} 取数成功但没有任何动态，暂不建立基线")
            return
        store.advance(uid, parsed_list[-1].dyn_id)
        await store.save()
        logger.info(
            f"UID {uid} 首次建立基线：{len(parsed_list)} 条历史动态标记为已读，本轮不推送"
        )
        return

    fresh = [p for p in parsed_list if not store.is_seen(uid, p.dyn_id)]
    if not fresh:
        logger.debug(f"UID {uid} 本轮无新动态")
        return

    to_push, overflow, stale = _select_pushable(uid, fresh)
    if to_push:
        logger.info(
            f"UID {uid} 发现 {len(to_push)} 条新动态，推送到 {len(targets)} 个群"
        )
    for parsed in to_push:
        # 推送前先写游标：渲染/发送失败最多丢这一条，不会每轮重推卡死；中途崩溃时后面的下轮照推
        store.advance(uid, parsed.dyn_id)
        await store.save()
        segments = await build_messages(parsed)  # 同一动态跨群只渲染一次
        for group_id in targets:
            await dispatch_segments(bot, SendTarget(group_id=group_id), segments)

    # 跳过类型、超窗口、超条数的也一并标记已读
    if store.advance(uid, fresh[-1].dyn_id):
        await store.save()

    if overflow:
        tip = Message(f"另有 {overflow} 条动态未展示")
        for group_id in targets:
            await _send_with_retry(
                bot, SendTarget(group_id=group_id), tip, desc="未展示提示"
            )
    if stale:
        logger.info(
            f"UID {uid} 另有 {stale} 条动态超出推送窗口，已静默标记已读（不提示群友）"
        )


async def poll_once() -> None:
    uids = store.uids()
    if not uids:
        return
    try:
        bot = get_bot()
    except ValueError:
        logger.debug("当前没有可用的 Bot 连接，跳过本轮 B 站动态轮询")
        return

    for index, uid in enumerate(uids):
        remaining = backoff.remaining()
        if remaining:
            logger.debug(f"B 站取数退避中（剩余 {remaining}s），跳过本轮")
            break
        if index and plugin_config.uid_request_gap_seconds:
            await asyncio.sleep(plugin_config.uid_request_gap_seconds)
        try:
            await _poll_uid(bot, uid)
        except Exception as e:
            # 轮询边界：单个 UID 的意外不能中断整轮
            logger.exception(f"UID {uid} 本轮处理失败: {get_exc_desc(e)}")
    await store.save_if_stale()


# ---------------------------------------------------------------- 登录态


async def check_login() -> None:
    """校验 sessdata；刚变成失效时私聊超管"""
    if not await credential_manager.verify_login():
        return
    try:
        bot = get_bot()
    except ValueError:
        logger.error("B 站登录态失效提醒没有发出：当前没有 Bot 连接")
        return
    text = (
        "【B站动态订阅】登录态失效\n"
        "已自动退化为匿名取数（功能不中断，但风控概率上升）。\n"
        "请重新获取小号的 SESSDATA 并填入 "
        "config/plugins/bili_dyn_sub/config.json 的 sessdata 字段后重启 bot。"
    )
    for superuser in bot.config.superusers:
        try:
            await bot.send_private_msg(user_id=int(superuser), message=text)
        except (ActionFailed, NetworkError) as e:
            logger.error(
                f"向超管 {superuser} 发送登录态失效提醒失败: {get_exc_desc(e)}"
            )


async def _startup_login_check() -> None:
    await asyncio.sleep(LOGIN_CHECK_STARTUP_DELAY_SECONDS)
    await check_login()


# ---------------------------------------------------------------- 注册


apscheduler.add_job(
    poll_once,
    trigger="interval",
    seconds=plugin_config.poll_interval_seconds,
    jitter=plugin_config.poll_jitter_seconds or None,
    id="bili_dyn_sub_poll",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
logger.info(
    f"B 站动态轮询任务已注册：间隔 {plugin_config.poll_interval_seconds}s"
    f"（jitter {plugin_config.poll_jitter_seconds}s），当前订阅 {len(store.uids())} 个 UID"
)

if plugin_config.sessdata.strip():
    apscheduler.add_job(
        check_login,
        trigger="interval",
        hours=LOGIN_CHECK_INTERVAL_HOURS,
        id="bili_dyn_sub_login_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    @on_plugin_startup(get_driver(), "bili_dyn_sub")
    async def _on_startup() -> None:
        # 不在启动钩子里直接 await：此刻 Bot 往往还没连上
        runtime.spawn(_startup_login_check(), name="bilibili-startup-login")

else:
    logger.info("未配置 sessdata，B 站动态订阅将以匿名方式取数")


@on_plugin_shutdown(get_driver(), "bili_dyn_sub")
async def _flush_state() -> None:
    await credential_manager.flush()
    await store.save()
