from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot

from ..utils.image_utils import image_segment
from ..utils.tools import ForwardItem, ForwardStatus, get_logger, send_forward_msg
from .config import plugin_config
from .parser import now_iso
from .store import save_state

logger = get_logger("juya_daily_fetcher.deliver")
FORWARD_NAME = "JUYA AI DAILY"


def _target_status(pending: dict[str, Any], target: str) -> dict[str, Any]:
    targets = pending.setdefault("targets", {})
    info = targets.get(target)
    if not isinstance(info, dict):
        info = {"summary_sent": False, "report_sent": False}
        targets[target] = info
    return info


async def send_summary(bot: Bot, summary: str, target: str) -> None:
    text = str(summary or "").strip()
    if not text:
        return
    await bot.send_group_msg(group_id=int(target), message=text)


async def send_report(bot: Bot, image_paths: list[Path], target: str) -> None:
    if not image_paths:
        raise RuntimeError("merged forward requires at least one image")
    missing = next((path for path in image_paths if not path.is_file()), None)
    if missing is not None:
        raise RuntimeError(f"rendered image is missing: {missing}")
    items = [
        ForwardItem(content=image_segment(path), name=FORWARD_NAME)
        for path in image_paths
    ]
    status = await send_forward_msg(
        bot,
        items=items,
        group_id=int(target),
        timeout=120.0,
        fallback_on_action_failed=False,
    )
    if status == ForwardStatus.TIMEOUT_UNKNOWN:
        raise RuntimeError("merged forward timed out with unknown result")


async def send_debug(bot: Bot, user_id: str, summary: str, image_paths: list[Path]) -> None:
    forbidden = set(plugin_config.targets)
    if user_id in forbidden:
        raise RuntimeError("debug target cannot be a production group")
    if user_id != plugin_config.debug_user_id:
        raise RuntimeError(f"debug target must be {plugin_config.debug_user_id}")
    if summary.strip():
        await bot.send_private_msg(user_id=int(user_id), message=summary.strip())
    items = [
        ForwardItem(content=image_segment(path), name=FORWARD_NAME)
        for path in image_paths
    ]
    status = await send_forward_msg(
        bot,
        items=items,
        user_id=int(user_id),
        timeout=120.0,
        fallback_on_action_failed=False,
    )
    if status == ForwardStatus.TIMEOUT_UNKNOWN:
        raise RuntimeError("debug merged forward timed out with unknown result")


async def deliver_pending(bot: Bot, state: dict[str, Any]) -> None:
    pending = state.get("pending")
    if not isinstance(pending, dict) or pending.get("kind") != "rss_fanout":
        raise RuntimeError("state contains an unsupported pending delivery")
    summary = str(pending.get("summary") or "").strip()
    raw_paths = pending.get("image_paths")
    image_paths = [Path(str(item)) for item in raw_paths if str(item).strip()] if isinstance(raw_paths, list) else []
    if not image_paths or not all(path.is_file() for path in image_paths):
        raise RuntimeError("state contains malformed RSS fanout")

    for target in plugin_config.targets:
        info = _target_status(pending, target)
        if summary and not info.get("summary_sent"):
            await send_summary(bot, summary, target)
            info.update({"summary_sent": True, "summary_sent_at": now_iso()})
            save_state(state)
            logger.info(f"delivered RSS summary to group {target}")
        elif not summary:
            info["summary_sent"] = True
        if not info.get("report_sent"):
            await send_report(bot, image_paths, target)
            info.update({"report_sent": True, "report_sent_at": now_iso()})
            save_state(state)
            logger.info(f"delivered {len(image_paths)} rendered RSS pages as one merged forward to group {target}")

    if all(
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
