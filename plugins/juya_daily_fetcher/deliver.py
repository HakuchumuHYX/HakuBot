from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import ActionFailed, NetworkError

from utils.onebot.media import image_segment
from utils.onebot.forward import ForwardItem, ForwardStatus, send_forward_msg
from utils.logging import get_exc_desc, get_logger
from plugins.juya_daily_fetcher.config import plugin_config
from plugins.juya_daily_fetcher.parser import now_iso
from plugins.juya_daily_fetcher.store import save_state

logger = get_logger("juya_daily_fetcher.deliver")
FORWARD_NAME = "JUYA AI DAILY"
SEND_RETRY_TIMES = 3
SEND_INTERVAL_SECONDS = 1.5


def forward_counts_as_sent(status: ForwardStatus) -> bool:
    return status in {
        ForwardStatus.SENT,
        ForwardStatus.FALLBACK_SENT,
        ForwardStatus.TIMEOUT_UNKNOWN,
    }


def _target_status(pending: dict[str, Any], target: str) -> dict[str, Any]:
    targets = pending.setdefault("targets", {})
    info = targets.get(target)
    if not isinstance(info, dict):
        info = {"summary_sent": False, "report_sent": False}
        targets[target] = info
    return info


def _onebot_bots() -> list[Bot]:
    try:
        driver = get_driver()
    except ValueError:
        return []
    return [bot for bot in driver.bots.values() if isinstance(bot, Bot)]


async def get_group_bot_map() -> dict[str, Bot]:
    """每个群只由第一个认领它的 Bot 负责，避免双开重复发送。"""
    mapping: dict[str, Bot] = {}
    for bot in _onebot_bots():
        try:
            groups = await bot.get_group_list()
        except Exception as exc:
            logger.error(f"获取机器人 {bot.self_id} 的群列表失败: {get_exc_desc(exc)}")
            continue
        for group in groups:
            if not isinstance(group, dict) or "group_id" not in group:
                continue
            group_id = str(group["group_id"])
            if group_id not in mapping:
                mapping[group_id] = bot
    return mapping


def _bot_for_target(target: str, bots_by_group: dict[str, Bot]) -> Bot:
    bot = bots_by_group.get(str(target))
    if bot is None:
        raise RuntimeError(f"没有 Bot 加入目标群 {target}")
    return bot


async def _send_group_text(bot: Bot, group_id: int, text: str) -> None:
    attempts = max(1, SEND_RETRY_TIMES)
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            await bot.send_group_msg(group_id=group_id, message=text)
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
            return
        except NetworkError as exc:
            last_error = get_exc_desc(exc)
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
            if "timeout" in str(exc).lower():
                logger.warning(
                    f"群 {group_id} 发送摘要超时，服务端可能已处理，跳过重试避免重复推送"
                )
                return
            logger.warning(
                f"群 {group_id} 发送摘要网络错误（第 {attempt}/{attempts} 次）: {last_error}"
            )
        except (ActionFailed, ValueError, asyncio.TimeoutError) as exc:
            last_error = get_exc_desc(exc)
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
            logger.warning(
                f"群 {group_id} 发送摘要失败（第 {attempt}/{attempts} 次）: {last_error}"
            )
    raise RuntimeError(f"群 {group_id} 发送摘要最终失败: {last_error}")


async def send_summary(bot: Bot, summary: str, target: str) -> None:
    text = str(summary or "").strip()
    if not text:
        return
    await _send_group_text(bot, int(target), text)


async def send_report(bot: Bot, image_paths: list[Path], target: str) -> None:
    if not image_paths:
        raise RuntimeError("合并转发至少需要一张图片")
    missing = next((path for path in image_paths if not path.is_file()), None)
    if missing is not None:
        raise RuntimeError(f"渲染图片缺失: {missing}")
    items = [
        ForwardItem(content=image_segment(path), name=FORWARD_NAME)
        for path in image_paths
    ]
    status = await send_forward_msg(
        bot,
        items=items,
        group_id=int(target),
        timeout=120.0,
        fallback_on_action_failed=True,
        fallback_interval=SEND_INTERVAL_SECONDS,
    )
    if status == ForwardStatus.TIMEOUT_UNKNOWN:
        logger.warning(f"群 {target} 合并转发超时，服务端可能已处理，视为发送成功")
        return
    if not forward_counts_as_sent(status):
        raise RuntimeError(f"群 {target} 合并转发未成功: {status}")
    await asyncio.sleep(SEND_INTERVAL_SECONDS)


async def send_debug(
    bot: Bot, user_id: str, summary: str, image_paths: list[Path]
) -> None:
    forbidden = set(plugin_config.targets)
    if user_id in forbidden:
        raise RuntimeError("debug 目标不能是生产群")
    if user_id != plugin_config.debug_user_id:
        raise RuntimeError(f"debug 目标必须是 {plugin_config.debug_user_id}")
    if summary.strip():
        await bot.send_private_msg(user_id=int(user_id), message=summary.strip())
        await asyncio.sleep(SEND_INTERVAL_SECONDS)
    items = [
        ForwardItem(content=image_segment(path), name=FORWARD_NAME)
        for path in image_paths
    ]
    status = await send_forward_msg(
        bot,
        items=items,
        user_id=int(user_id),
        timeout=120.0,
        fallback_on_action_failed=True,
        fallback_interval=SEND_INTERVAL_SECONDS,
    )
    if status == ForwardStatus.TIMEOUT_UNKNOWN:
        logger.warning(f"私聊 {user_id} 合并转发超时，服务端可能已处理，视为发送成功")
        return
    if not forward_counts_as_sent(status):
        raise RuntimeError(f"debug 合并转发未成功: {status}")


async def deliver_pending(
    state: dict[str, Any],
    *,
    bots_by_group: dict[str, Bot] | None = None,
) -> None:
    pending = state.get("pending")
    if not isinstance(pending, dict) or pending.get("kind") != "rss_fanout":
        raise RuntimeError("状态里的 pending 投递不受支持")
    summary = str(pending.get("summary") or "").strip()
    raw_paths = pending.get("image_paths")
    image_paths = (
        [Path(str(item)) for item in raw_paths if str(item).strip()]
        if isinstance(raw_paths, list)
        else []
    )
    if not image_paths or not all(path.is_file() for path in image_paths):
        raise RuntimeError("pending 图片缺失或格式不正确")

    if bots_by_group is None:
        bots_by_group = await get_group_bot_map()

    for index, target in enumerate(plugin_config.targets):
        if index > 0:
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
        bot = _bot_for_target(target, bots_by_group)
        info = _target_status(pending, target)
        if summary and not info.get("summary_sent"):
            await send_summary(bot, summary, target)
            info.update({"summary_sent": True, "summary_sent_at": now_iso()})
            save_state(state)
            logger.info(f"已向群 {target} 发送早报摘要")
        elif not summary:
            info["summary_sent"] = True
        if not info.get("report_sent"):
            await send_report(bot, image_paths, target)
            info.update({"report_sent": True, "report_sent_at": now_iso()})
            save_state(state)
            logger.info(f"已向群 {target} 合并转发 {len(image_paths)} 页早报长图")

    if plugin_config.targets and all(
        isinstance(pending.get("targets", {}).get(target), dict)
        and pending["targets"][target].get("summary_sent")
        and pending["targets"][target].get("report_sent")
        for target in plugin_config.targets
    ):
        state["seen"] = pending.get("seen_after", state.get("seen", {}))
        state["pending"] = None
        state["last_notified_at"] = now_iso()
        state["last_delivery_targets"] = list(plugin_config.targets)
        save_state(state)
