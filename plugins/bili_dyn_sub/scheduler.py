"""
bili_dyn_sub 调度层：UID 串行轮转 + 错误分流 + 补推闸门 + 复刻 bison 发送节奏

职责（设计文档 §6 / §4.2 / §4.3 / §5.2）：
- APScheduler interval job：间隔 poll_interval_seconds + jitter，max_instances=1 防重入
- **UID 之间串行且错开** uid_request_gap_seconds，绝不在同一 tick 并发打多个请求
- 取数异常按类型分流到 per-UID 退避；**任何异常都不推进 seen 状态**（§3.5）
- 首轮只建基线不回推历史（§4.2）；跳过类型/超期/超量的动态只标 seen 不推送（§4.3），
  其中"超出推送窗口"的停机积压**静默**丢弃（只记日志），只有防刷屏压下去的条数才提示群友
- 推送前先写 seen（渲染失败最多重复 1 条，不会永久卡死）；同一动态跨群只渲染一次（§5.3）
- 推送目标 = `store.get_groups(uid)`：**订阅关系即唯一开关**，不再叠加任何群级开关
  （为何不接群开关插件见 docs/bili_dyn_sub_design.md §11.4.3）
- 登录态由 nav 接口周期确认（配了 sessdata 才注册该 job），**按状态跃迁**私聊提醒超管换
  cookie —— 取数接口对失效 sessdata 只会静默退化为匿名请求，判不出登录态

借鉴来源：nonebot-bison (MIT, Copyright (c) 2021 felinae98)
- `send.py:71-84` 的拆包语义（首段单发 / 余下 1 段单发 / ≥2 段合并转发）
- `send.py:15` 的 `MESSGE_SEND_INTERVAL = 1.5` 全局发送间隔与 `bison_resend_times` 重试次数
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, Union

from nonebot import get_bot, get_driver, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.exception import ActionFailed, NetworkError

require("nonebot_plugin_apscheduler")
# 本模块自身就叫 scheduler，若直接 `from nonebot_plugin_apscheduler import scheduler`
# 会与包属性 plugins.bili_dyn_sub.scheduler 同名互相遮蔽（group_statistics 踩过这个坑），
# 故一律用 apscheduler 别名引用外部调度器。
from nonebot_plugin_apscheduler import scheduler as apscheduler

from ..utils.tools import get_exc_desc, get_logger, run_in_pool
from . import api
from .backoff import ACTION_REFRESH_COOKIE, backoff_manager
from .config import plugin_config
from .credential import LoginStatus, credential_manager
from .parser import ParsedDynamic, parse_feed, should_skip
from .render import build_messages
from .store import dyn_id_to_int, store

logger = get_logger("bili_dyn_sub.scheduler")

POLL_JOB_ID = "bili_dyn_sub_poll"
PRUNE_JOB_ID = "bili_dyn_sub_prune"
LOGIN_CHECK_JOB_ID = "bili_dyn_sub_login_check"

# 登录态校验周期：SESSDATA 有效期以月计，6 小时一次足够及时且几乎无成本
# （credential.verify_login 自带 30 分钟缓存，重复调用不会真的发请求）
LOGIN_CHECK_INTERVAL_HOURS = 6
# 启动后延迟这么久再校验一次：等适配器把 Bot 连上，失效告警才发得出去
LOGIN_CHECK_STARTUP_DELAY_SECONDS = 30.0

# 空 feed（code==0 且 items 为空，是合法结果）建基线用的哨兵游标：
# store.is_baseline_initialized 要求 cursor>0，若用 0 建基线等于没建，
# 该 UP 将来的第一条动态会被当成"建基线"静默吞掉。
_EMPTY_FEED_CURSOR = 1

# 实测发现 B 站会间歇性对同一个 UID 返回 `code=0 / items=[] / total="0" / has_more=false`
# （软风控，与"该 UP 确实没动态"在单次响应上无法区分；相邻请求又能正常返回 13 条）。
# 这种响应若被用来建基线，会写下哨兵游标 cursor=1 → 下一次正常取数时全部历史动态都"比游标新"，
# 于是被补推闸门放过的最近几条会被当成新动态推出去（bison 故障模式①的翻版）。
# 因此建基线要求连续观察到 N 次空 feed，才认定这个 UP 真的没有历史动态。
_EMPTY_FEED_BASELINE_THRESHOLD = 3
# 基线已建立时，连续这么多轮空 feed 就告警一次：既没有 -352 也没有动态，
# 大概率是刚重造的 cookie 处在软风控里（实测新 buvid3 会被返回空 feed，养一段时间自愈）
_EMPTY_FEED_WARN_THRESHOLD = 5
# per-UID 的连续空 feed 计数（仅内存，进程重启后重新计数）
_empty_feed_streak: dict[str, int] = {}

# 发送失败时的兜底重试上下限，防止 config 被填成 0/负值
_MIN_SEND_ATTEMPTS = 1

# 合并转发的 Bot 身份（uin, nickname），首次用到时取一次
_self_identity: Optional[tuple[str, str]] = None


# ---------------------------------------------------------------- 配置兜底


def _cfg_float(name: str, default: float, minimum: float = 0.0) -> float:
    """读取 float 配置项并做下限保护（config.py 未加数值 validator）"""
    try:
        value = float(getattr(plugin_config, name, default))
    except (TypeError, ValueError):
        logger.warning(f"配置项 {name} 非法，回落默认值 {default}")
        return default
    return max(minimum, value)


def _cfg_int(name: str, default: int, minimum: int = 0) -> int:
    """读取 int 配置项并做下限保护"""
    try:
        value = int(getattr(plugin_config, name, default))
    except (TypeError, ValueError):
        logger.warning(f"配置项 {name} 非法，回落默认值 {default}")
        return default
    return max(minimum, value)


async def _save_state() -> None:
    """把去重状态落盘（同步原子写下线程池，不阻塞事件循环）"""
    await run_in_pool(store.save)


# ---------------------------------------------------------------- 取数与分流


def _log_error(uid: str, error_key: str, message: str) -> None:
    """日志纪律（§3.5）：同一错误码只在状态跃迁时 warn，连续复发降级为 debug"""
    if backoff_manager.should_log_warning(uid, error_key):
        logger.warning(message)
    else:
        logger.debug(message)


# ---------------------------------------------------------------- 登录态校验
#
# 为什么不靠取数接口判断登录态：实测用伪造的 SESSDATA 打 feed/space 依然返回 code=0 并
# 正常吐 13 条动态 —— 失效的登录态会被**静默降级成匿名行为**而不是报 -101，所以「-101 即
# sessdata 过期」是猜测，对 sessdata 过期这一最常见场景永远不会触发。
# 唯一权威判据是 nav 接口（credential.verify_login，返回 data.isLogin / data.uname）。
#
# 告警策略：按**状态跃迁**而非时间节流。同一次失效只打扰超管一次（除非期间恢复过），
# 恢复时只打 info 日志不打扰用户。error 非空（网络抖动/限流）一律不算失效、不改跃迁基线。

# 上一次**已确认**的登录态：None=尚未确认（含未配置 sessdata），True=有效，False=已失效。
# 仅存内存：进程重启后会重新确认一次，此时若仍失效会再提醒一次（重启即提醒，符合直觉）。
_last_known_login: Optional[bool] = None

# 取数 -101 触发的**强制**校验冷却：-101 不进退避、会每轮复发，不加冷却会把 nav 打成高频请求
FORCED_LOGIN_CHECK_COOLDOWN_SECONDS = 600.0
_last_forced_login_check_ts: float = 0.0

# 启动时那次校验的后台任务引用（防止任务被 GC 提前回收）
_startup_login_task: Optional[asyncio.Task] = None


def _build_login_expired_text(status: LoginStatus) -> str:
    """失效告警文案：说清后果（功能不中断但风控概率上升）与具体处置动作"""
    return (
        "【B站动态订阅】登录态失效\n"
        f"状态：{status.summary()}\n"
        "已自动退化为匿名取数（功能不中断，但风控概率上升，可能出现 -352 空轮）。\n"
        "请重新获取小号的 SESSDATA 并填入 "
        "plugins/bili_dyn_sub/config.json 的 sessdata 字段后重启 bot。"
    )


async def _notify_superusers_login_expired(status: LoginStatus) -> bool:
    """私聊提醒超管更换 sessdata。

    返回 False 仅代表「本次没能提醒、之后应重试」（当前无 Bot 连接）；
    未配置 superusers 属于配置问题、重试也没用，按已提醒处理避免每轮刷日志。
    """
    text = _build_login_expired_text(status)
    try:
        bot = get_bot()
    except (ValueError, KeyError) as e:
        logger.warning(f"登录态失效提醒暂时无法发送（无可用 Bot 连接），下次校验再试: {get_exc_desc(e)}")
        return False

    superusers = getattr(bot.config, "superusers", None) or []
    if not superusers:
        logger.warning("B 站登录态已失效，但未配置 superusers，无法私聊提醒")
        return True

    for superuser in superusers:
        try:
            await bot.send_private_msg(user_id=int(superuser), message=text)
        except (ValueError, TypeError) as e:
            logger.warning(f"超管 QQ 号 {superuser!r} 非法，跳过提醒: {get_exc_desc(e)}")
        except (ActionFailed, NetworkError) as e:
            logger.warning(f"向超管 {superuser} 发送登录态失效提醒失败: {get_exc_desc(e)}")
    return True


async def _handle_login_transition(status: LoginStatus) -> None:
    """按状态跃迁决定是否告警（有效→失效私聊超管；失效→有效只记日志）"""
    global _last_known_login

    if not status.configured:
        # 未配置 sessdata：匿名取数是预期行为，没有「失效」可言
        _last_known_login = None
        return
    if status.error:
        # 网络抖动 / 限流 / 未知业务码：不是掉登录的证据（credential 已打 warning），不动基线
        return

    if status.is_login:
        if _last_known_login is False:
            logger.info(f"B 站登录态已恢复（uname={status.uname or '-'}），无需再提醒超管")
        _last_known_login = True
        return

    # 到这里是「配了 sessdata + 校验成功 + isLogin=false」，即确定需要人工更换
    if _last_known_login is False:
        logger.debug("B 站登录态仍处于失效状态，已提醒过超管，不重复打扰")
        return
    logger.warning(
        "B 站登录态失效：取数已退化为匿名请求（功能不中断但更易被风控），正在私聊提醒超管更换 sessdata"
    )
    if await _notify_superusers_login_expired(status):
        _last_known_login = False


async def check_login_status(*, force: bool = False) -> LoginStatus:
    """校验登录态并按跃迁触发告警；返回本次结论。

    force=True 用于「怀疑掉登录」的即时确认（如取数意外返回 -101），会绕过 30 分钟缓存。
    未配置 sessdata 时 credential 侧不发任何请求，本函数等价于零开销。
    """
    status = await credential_manager.verify_login(force=force)
    await _handle_login_transition(status)
    return status


async def confirm_login_after_auth_error() -> LoginStatus:
    """取数返回 -101 时向 nav 确认真实登录态；**带冷却**，避免每轮都打 nav。

    -101 不会自动进退避（它不是风控），所以出现一次就会每轮复发。若每次都
    `force=True`，100s 的轮询间隔 × 每个 UID 会把 nav 打成一个高频请求（旧实现的
    「6 小时节流」在改成状态跃迁告警后一并丢掉了，这里补回节流，只是尺度小得多）。
    冷却窗口内退回普通校验：命中 30 分钟缓存则一个请求都不发，结论照样驱动跃迁告警。
    """
    global _last_forced_login_check_ts
    now = time.time()
    force = now - _last_forced_login_check_ts >= FORCED_LOGIN_CHECK_COOLDOWN_SECONDS
    if force:
        _last_forced_login_check_ts = now
    return await check_login_status(force=force)


async def login_check_job() -> None:
    """周期任务：确认 sessdata 是否还在登录态（仅在配置了 sessdata 时注册）"""
    status = await check_login_status()
    logger.debug(f"B 站登录态周期校验完成：{status.summary()}")


async def _startup_login_check() -> None:
    """启动后台校验：等 Bot 连上再校验一次，并把结论写进日志"""
    await asyncio.sleep(LOGIN_CHECK_STARTUP_DELAY_SECONDS)
    try:
        status = await check_login_status()
    except Exception as e:
        # 后台任务边界：任何意外都不能变成「Task exception was never retrieved」
        logger.error(f"启动时校验 B 站登录态失败: {get_exc_desc(e)}")
        return
    if not status.configured:
        logger.info("未配置 sessdata，B 站动态订阅将以匿名方式取数（风控概率略高）")
    elif status.error:
        logger.warning(f"启动时无法确认 B 站登录态：{status.summary()}")
    elif status.is_login:
        logger.info(f"B 站登录态有效，账号={status.uname or '-'}")
    else:
        logger.warning("已配置 sessdata 但登录态无效，将退化为匿名取数（已提醒超管更换）")


async def _fetch_feed(uid: str) -> Optional[dict[str, Any]]:
    """取一次 feed；成功返回 data 段，任何失败返回 None（调用方一律不推进状态）"""
    force_refresh = False
    for attempt in (1, 2):
        try:
            return await api.fetch_space_feed(uid, force_refresh_cookie=force_refresh)
        except api.BiliRiskControlError as e:
            action = backoff_manager.on_risk_control(uid, "-352")
            if action == ACTION_REFRESH_COOKIE and attempt == 1:
                logger.info(f"UID {uid} 触发 -352 风控，强制重造 cookie 后重试一次")
                force_refresh = True
                continue
            # 刷新 cookie 后仍风控（或已达刷新上限）→ 进退避，本轮放弃
            remaining = backoff_manager.remaining_seconds(uid)
            tail = f"退避 {remaining}s" if remaining else "下轮继续尝试重造 cookie"
            _log_error(
                uid,
                "-352",
                f"UID {uid} 持续被 -352 风控（{e}），{tail}；"
                "长期不恢复请在 config.json 配置 sessdata（小号登录态）",
            )
            return None
        except api.BiliIpBlockedError as e:
            backoff_manager.on_ip_block(uid, "HTTP 412")
            _log_error(
                uid,
                "412",
                f"UID {uid} 命中 IP 层风控（{e}），换 cookie 无效，"
                f"退避 {backoff_manager.remaining_seconds(uid)}s；请在 config.json 配置 proxy",
            )
            return None
        except api.BiliCaptchaError as e:
            # 需要 geetest 人机验证：不尝试自动过验证码，放弃本轮
            _log_error(
                uid, "captcha", f"UID {uid} 需要人机验证（{e}），放弃本轮；建议在 config.json 配置 sessdata"
            )
            return None
        except api.BiliAuthError as e:
            # -101 只是「这次请求被当成未登录」，不能直接断言 sessdata 过期：
            # feed/space 对失效 sessdata 返回的是 code=0（静默降级为匿名），反过来 -101 也可能
            # 只是本次请求未带上登录字段。真实状态问 nav 接口，是否告警交给状态跃迁逻辑判断。
            _log_error(uid, "-101", f"UID {uid} 取数返回 -101（{e}），正在向 nav 接口确认真实登录态")
            await confirm_login_after_auth_error()
            return None
        except api.BiliSignError as e:
            api.invalidate_wbi_keys()
            if attempt == 1:
                logger.info(f"UID {uid} wbi 签名被拒（{e}），已作废 key 缓存后重试一次")
                continue
            _log_error(uid, "-403", f"UID {uid} 刷新 wbi key 后仍签名失败（{e}），放弃本轮")
            return None
        except api.BiliNetworkError as e:
            backoff_manager.on_network_error(uid, get_exc_desc(e))
            _log_error(
                uid,
                "network",
                f"UID {uid} 取数网络异常（{e}），退避 {backoff_manager.remaining_seconds(uid)}s 后重试",
            )
            return None
        except api.BiliApiError as e:
            # 其他错误码 / 响应结构异常：不当作"没有动态"，本轮直接放弃
            _log_error(uid, f"api:{e.code}", f"UID {uid} 取数失败（{e}），本轮不推进状态")
            return None
    return None


# ---------------------------------------------------------------- 判新与闸门


def _track_empty_feed(uid: str, parsed_list: list[ParsedDynamic]) -> int:
    """维护 per-UID 的连续空 feed 计数并在基线已建立时按阈值告警，返回当前连续次数。

    「code==0 且一条动态都没有」既可能是该 UP 真没动态，也可能是软风控
    （实测同一 UID 相邻两次请求分别返回 13 条与 0 条；刚重造的 buvid3 尤其容易吃这个）。
    基线已建立时它表现为「一直没有新动态」，不报错也不退避，需要日志给出线索。
    """
    if parsed_list:
        _empty_feed_streak.pop(uid, None)
        return 0

    streak = _empty_feed_streak.get(uid, 0) + 1
    _empty_feed_streak[uid] = streak
    if store.is_baseline_initialized(uid):
        message = (
            f"UID {uid} 已连续 {streak} 轮取数成功但 feed 为空（既无 -352 也无动态），"
            f"疑似软风控；cookie 会随时间自愈，长期不恢复请在 config.json 配置 sessdata"
        )
        if streak == _EMPTY_FEED_WARN_THRESHOLD or (
            streak > _EMPTY_FEED_WARN_THRESHOLD and streak % _EMPTY_FEED_WARN_THRESHOLD == 0
        ):
            logger.warning(message)
        else:
            logger.debug(message)
    return streak


def _init_baseline(uid: str, parsed_list: list[ParsedDynamic]) -> None:
    """首次订阅 / 状态缺失：全部标 seen，只 log 不推送（设计文档 §4.2）。

    空 feed 不立即建基线（见 _EMPTY_FEED_BASELINE_THRESHOLD 的说明）：
    连续 N 轮都是空的才认定这个 UP 真没有历史动态并写哨兵游标。
    """
    ids = [p.dyn_id for p in parsed_list if p.dyn_id]
    if not ids:
        streak = _empty_feed_streak.get(uid, 0)
        if streak < _EMPTY_FEED_BASELINE_THRESHOLD:
            logger.info(
                f"UID {uid} 取数成功但没有任何动态（第 {streak}/{_EMPTY_FEED_BASELINE_THRESHOLD} 次），"
                "可能是软风控而非真的没动态，暂不建立基线"
            )
            return
        store.init_baseline(uid, [], _EMPTY_FEED_CURSOR)
        _empty_feed_streak.pop(uid, None)
        logger.info(
            f"UID {uid} 连续 {streak} 轮均无历史动态，已用哨兵游标建立基线，之后的新动态会正常推送"
        )
        return
    cursor = max((dyn_id_to_int(i) or 0) for i in ids)
    store.init_baseline(uid, ids, cursor)
    logger.info(f"UID {uid} 首次建立基线：{len(ids)} 条历史动态标记为已读，本轮不推送")


class PushSelection(NamedTuple):
    """一轮筛选的结果。

    两个计数**故意分开**，因为它们的对外语义完全不同：
    - overflow：本轮新动态太多、被单轮条数上限压下去的条数 → 会向群里提示"另有 X 条未展示"，
      因为群友确实"错过了刚发生的事"，不提示反而像漏推；
    - stale：超出推送窗口（停机期间的积压）被丢弃的条数 → **只记日志，绝不进群消息**。
      默认窗口的全部意义就是"重启/迁移对群友无感"（见 config.max_dynamic_age_minutes），
      若还发一条"另有 X 条动态未展示"，等于把本该无声的停机公告出去，与该需求直接矛盾。
    """

    to_push: list[ParsedDynamic]
    overflow: int
    stale: int


def _select_pushable(uid: str, parsed_list: list[ParsedDynamic]) -> PushSelection:
    """筛出真正要推的动态（升序）；被跳过/闸掉的一律只标 seen。

    两个抑制计数的差别见 PushSelection。二者都不含"跳过类型"与"未订阅分类"，
    那两类不属于"未展示的动态"，无需向用户提示。
    """
    fresh = [p for p in parsed_list if p.dyn_id and not store.is_seen(uid, p.dyn_id)]
    if not fresh:
        return PushSelection([], 0, 0)

    categories = set(store.get_categories(uid))
    candidates: list[ParsedDynamic] = []
    for parsed in fresh:
        if should_skip(parsed):
            # 直播推荐/广告等：跳过推送但同样推进状态，否则每轮重复处理（§4.2）
            logger.debug(f"UID {uid} 动态 {parsed.dyn_id} 类型 {parsed.dyn_type} 跳过推送，仅标记已读")
            store.mark_seen(uid, parsed.dyn_id, save=False)
            continue
        if parsed.category and parsed.category not in categories:
            logger.debug(f"UID {uid} 动态 {parsed.dyn_id} 分类 {parsed.category} 未订阅，仅标记已读")
            store.mark_seen(uid, parsed.dyn_id, save=False)
            continue
        candidates.append(parsed)

    stale = 0
    overflow = 0

    # 闸一：推送窗口 —— 一条动态最多能"旧"到什么程度还值得推（max_dynamic_age_minutes）。
    # 「要不要补推停机期间遗漏的动态」不是独立开关，只是这个窗口取多大的自然结果：
    # 30 → 只推新鲜动态（停机期间遗漏的自然被丢弃）；1440 → 补推一天内的；0 → 不做时间限制。
    # 超窗口的一律**静默**标记已读：只记日志、不进群消息，避免下一轮反复处理。
    age_limit_seconds = _cfg_int("max_dynamic_age_minutes", 30, 0) * 60
    if age_limit_seconds > 0 and candidates:
        deadline = time.time() - age_limit_seconds
        kept: list[ParsedDynamic] = []
        for parsed in candidates:
            if parsed.pub_ts and parsed.pub_ts < deadline:
                logger.info(
                    f"UID {uid} 动态 {parsed.dyn_id} 发布已超 {age_limit_seconds // 60}min，"
                    "超出推送窗口，仅标记已读"
                )
                store.mark_seen(uid, parsed.dyn_id, save=False)
                stale += 1
            else:
                kept.append(parsed)
        candidates = kept

    # 闸二：单 UID 单轮最多推 N 条，多出的（较旧的那些）只标 seen（0 表示不限）。
    # 这是与"多旧才推"无关的独立关注点：防刷屏，且这部分会向群里提示"另有 X 条未展示"。
    max_count = _cfg_int("max_push_per_round", 5, 0)
    if max_count > 0 and len(candidates) > max_count:
        extra = candidates[:-max_count]
        candidates = candidates[-max_count:]
        for parsed in extra:
            store.mark_seen(uid, parsed.dyn_id, save=False)
        overflow = len(extra)
        logger.info(f"UID {uid} 本轮新动态超过 {max_count} 条上限，较旧的 {overflow} 条仅标记已读")

    return PushSelection(candidates, overflow, stale)


# ---------------------------------------------------------------- 发送节奏


async def _self_info(bot: Bot) -> tuple[str, str]:
    """合并转发节点用的 Bot 身份，取一次后进程内复用"""
    global _self_identity
    if _self_identity is not None:
        return _self_identity
    uin, nickname = str(bot.self_id), "Bot"
    try:
        info = await bot.get_login_info()
        uin = str(info.get("user_id", bot.self_id))
        nickname = str(info.get("nickname") or "Bot")
    except (ActionFailed, NetworkError, asyncio.TimeoutError) as e:
        logger.debug(f"获取登录信息失败，合并转发使用兜底身份: {get_exc_desc(e)}")
    _self_identity = (uin, nickname)
    return _self_identity


@dataclass(frozen=True, slots=True)
class SendTarget:
    """一个发送目标：群聊或私聊二选一。

    推送走群聊；`b站订阅测试` 的渲染预览走私聊（把真实推送效果发给发起人自己，
    不打扰任何生产群）。两者共用同一套重试/间隔/合并转发降级逻辑，
    以保证预览与真实推送的分包节奏**逐条一致**。
    """

    group_id: Optional[int] = None
    user_id: Optional[int] = None

    @property
    def is_private(self) -> bool:
        return self.user_id is not None

    def __str__(self) -> str:
        return f"私聊 {self.user_id}" if self.is_private else f"群 {self.group_id}"


async def _send_with_retry(
    bot: Bot,
    target: SendTarget,
    payload: Union[Message, list[dict[str, Any]]],
    *,
    forward: bool = False,
    desc: str = "",
) -> bool:
    """发一条消息，失败重试 send_retry_times 次；每次发送后固定 sleep 全局间隔。

    返回 False 仅代表"确认失败"（调用方可安全降级重发）；
    超时属于"可能已送达"，返回 True 以避免重复推送（同 utils.tools.send_forward_msg 的判断）。
    """
    attempts = max(_MIN_SEND_ATTEMPTS, _cfg_int("send_retry_times", 3, _MIN_SEND_ATTEMPTS))
    interval = _cfg_float("send_interval_seconds", 1.5, 0.0)

    for attempt in range(1, attempts + 1):
        try:
            if forward:
                api_name = "send_private_forward_msg" if target.is_private else "send_group_forward_msg"
                kwargs = (
                    {"user_id": target.user_id} if target.is_private else {"group_id": target.group_id}
                )
                await bot.call_api(api_name, messages=payload, **kwargs)
            elif target.is_private:
                await bot.send_private_msg(user_id=target.user_id, message=payload)
            else:
                await bot.send_group_msg(group_id=target.group_id, message=payload)
        except NetworkError as e:
            await asyncio.sleep(interval)
            if "timeout" in str(e).lower():
                logger.warning(f"{target} 发送{desc}超时，服务端可能已处理，跳过重试避免重复推送")
                return True
            logger.warning(f"{target} 发送{desc}网络错误（第 {attempt}/{attempts} 次）: {get_exc_desc(e)}")
        except (ActionFailed, ValueError, asyncio.TimeoutError) as e:
            await asyncio.sleep(interval)
            logger.warning(f"{target} 发送{desc}失败（第 {attempt}/{attempts} 次）: {get_exc_desc(e)}")
        else:
            # 全局节奏：发送成功后也要间隔，避免连发触发风控/限速
            await asyncio.sleep(interval)
            return True

    logger.error(f"{target} 发送{desc}最终失败，已重试 {attempts} 次")
    return False


async def dispatch_segments(bot: Bot, target: SendTarget, segments: list[MessageSegment]) -> None:
    """按 §5.2 的拆包语义分发：首段单发 → 余下 1 段单发 / ≥2 段合并转发。

    公开给 `__init__.py` 的渲染预览复用：预览与真实推送必须走同一条分发路径，
    否则"预览看着没问题"就证明不了"推到群里也没问题"。
    """
    if not segments:
        return

    await _send_with_retry(bot, target, Message(segments[0]), desc="动态文字卡片")

    rest = segments[1:]
    if not rest:
        return
    if len(rest) == 1:
        await _send_with_retry(bot, target, Message(rest[0]), desc="动态配图")
        return

    uin, nickname = await _self_info(bot)
    nodes = [
        {"type": "node", "data": {"name": nickname, "uin": uin, "content": Message(segment)}}
        for segment in rest
    ]
    ok = await _send_with_retry(
        bot, target, nodes, forward=True, desc=f"{len(rest)} 张配图（合并转发）"
    )
    if not ok:
        # 合并转发被拒时逐张补发，宁可丑不可漏
        logger.info(f"{target} 合并转发失败，降级为逐张发送 {len(rest)} 张配图")
        for segment in rest:
            await _send_with_retry(bot, target, Message(segment), desc="动态配图（降级）")


# ---------------------------------------------------------------- 主循环


async def _poll_uid(bot: Bot, uid: str) -> None:
    """处理单个 UID：取数 → 判新 → 闸门 → 渲染 → 分发"""
    if backoff_manager.is_backing_off(uid):
        logger.debug(f"UID {uid} 仍在退避中（剩余 {backoff_manager.remaining_seconds(uid)}s），跳过本轮")
        return

    data = await _fetch_feed(uid)
    if data is None:
        return  # 风控/网络/解析失败一律不动游标、不写 seen（§3.5）

    backoff_manager.on_success(uid)
    store.touch_last_success(uid, save=False)
    parsed_list = parse_feed(data)
    _track_empty_feed(uid, parsed_list)

    if not store.is_baseline_initialized(uid):
        _init_baseline(uid, parsed_list)  # 内部已落盘
        return

    to_push, overflow, stale = _select_pushable(uid, parsed_list)
    # 先把「只标 seen 不推」的部分与 last_success 落盘
    await _save_state()

    if not to_push:
        if stale or overflow:
            logger.info(
                f"UID {uid} 本轮新动态全部被闸门抑制（超窗口 {stale} 条 / 超条数上限 {overflow} 条），无需推送"
            )
        else:
            logger.debug(f"UID {uid} 本轮无新动态")
        return

    # 推送目标**完全由订阅关系决定**：订阅关系即唯一开关，不再叠加任何群级开关。
    # 双层控制会造成"订阅列表里有、就是不推"的诡异状态（且超管一句「禁用all」就能让订阅静默失效），
    # 「不想收了」的正确操作是「b站退订」。完整理由见 docs/bili_dyn_sub_design.md §11.4.3。
    targets = store.get_groups(uid)
    if not targets:
        for parsed in to_push:
            store.mark_seen(uid, parsed.dyn_id, save=False)
        await _save_state()
        logger.info(f"UID {uid} 有 {len(to_push)} 条新动态，但已无订阅群（可能刚被退订），仅标记已读")
        return

    logger.info(f"UID {uid} 发现 {len(to_push)} 条新动态，推送到 {len(targets)} 个群")
    for parsed in to_push:
        # 推送前先写状态：渲染/发送失败最多重复 1 条，不会像 haruka-bot 那样永久卡死（§4.2）
        store.mark_seen(uid, parsed.dyn_id, save=False)
        await _save_state()
        # 同一动态跨群只渲染一次（§5.3）
        segments = await build_messages(parsed)
        for group_id in targets:
            await dispatch_segments(bot, SendTarget(group_id=group_id), segments)

    # 只有「刷屏保护压下去的条数」才提示群友；超出推送窗口的停机积压保持静默
    # （默认窗口的目的就是让重启/迁移对群友无感，提示一句等于把停机公告出去）
    if overflow > 0:
        tip = Message(f"另有 {overflow} 条动态未展示")
        for group_id in targets:
            await _send_with_retry(bot, group_id, tip, desc="未展示提示")
    if stale > 0:
        logger.info(f"UID {uid} 另有 {stale} 条动态超出推送窗口，已静默标记已读（不提示群友）")


async def poll_once() -> None:
    """一轮完整轮询：UID 之间串行且错开，单个 UID 失败不影响其他 UID"""
    uids = store.get_all_uids()
    if not uids:
        logger.debug("暂无 B 站动态订阅，跳过本轮轮询")
        return

    try:
        bot = get_bot()
    except (ValueError, KeyError) as e:
        logger.info(f"当前没有可用的 Bot 连接，跳过本轮 B 站动态轮询: {get_exc_desc(e)}")
        return

    gap = _cfg_float("uid_request_gap_seconds", 8.0, 0.0)
    for index, uid in enumerate(uids):
        if index > 0 and gap > 0:
            # 绝不在同一 tick 并发打多个请求（设计文档 §6）
            await asyncio.sleep(gap)
        try:
            await _poll_uid(bot, uid)
        except Exception as e:
            # 轮询边界：单个 UID 的任何意外都不能中断整轮
            logger.exception(f"UID {uid} 本轮处理失败: {get_exc_desc(e)}")


async def prune_state() -> None:
    """每日裁剪：清理已退订 uid 的残留状态与超限 seen_ids（§4.1）"""
    removed = await run_in_pool(store.prune)
    if removed:
        logger.info(f"去重状态裁剪完成，清理 {removed} 条记录")


# ---------------------------------------------------------------- 定时任务注册


def _register_jobs() -> None:
    """注册轮询与裁剪任务（import 即注册，幂等）"""
    interval = _cfg_int("poll_interval_seconds", 100, 30)
    jitter = _cfg_int("poll_jitter_seconds", 20, 0)
    try:
        apscheduler.add_job(
            poll_once,
            trigger="interval",
            seconds=interval,
            jitter=jitter or None,
            id=POLL_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        apscheduler.add_job(
            prune_state,
            trigger="cron",
            hour=4,
            minute=10,
            id=PRUNE_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    except (ValueError, TypeError, LookupError) as e:
        logger.error(f"注册 B 站动态轮询任务失败: {get_exc_desc(e)}")
        return
    logger.info(
        f"B 站动态轮询任务已注册：间隔 {interval}s（jitter {jitter}s），"
        f"当前订阅 {len(store.get_all_uids())} 个 UID"
    )


def _register_login_check() -> None:
    """注册登录态校验：启动后确认一次 + 每 LOGIN_CHECK_INTERVAL_HOURS 小时确认一次。

    周期 job **仅在配置了 sessdata 时注册**：匿名取数没有「登录态失效」这回事，
    不配就一个 job、一次请求都不产生（启动那次校验在未配置时也是纯内存操作，
    只用来在日志里说明「本次以匿名方式取数」）。
    """
    if (plugin_config.sessdata or "").strip():
        try:
            apscheduler.add_job(
                login_check_job,
                trigger="interval",
                hours=LOGIN_CHECK_INTERVAL_HOURS,
                id=LOGIN_CHECK_JOB_ID,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        except (ValueError, TypeError, LookupError) as e:
            logger.error(f"注册 B 站登录态校验任务失败: {get_exc_desc(e)}")
        else:
            logger.info(
                f"B 站登录态校验任务已注册：每 {LOGIN_CHECK_INTERVAL_HOURS} 小时确认一次 sessdata 是否有效"
            )
    else:
        logger.debug("未配置 sessdata，不注册登录态校验周期任务（匿名取数无登录态可校验）")

    try:
        driver = get_driver()
    except ValueError as e:
        # 脱离 bot.py 单跑插件代码时没有 driver，周期任务仍在，只是少一次启动校验
        logger.debug(f"当前没有 driver，跳过启动时的登录态校验: {get_exc_desc(e)}")
        return

    async def _on_startup() -> None:
        # 不能在启动钩子里直接 await：nav 请求最长 10s，且此刻 Bot 往往还没连上
        global _startup_login_task
        _startup_login_task = asyncio.create_task(_startup_login_check())

    driver.on_startup(_on_startup)


_register_jobs()
_register_login_check()
