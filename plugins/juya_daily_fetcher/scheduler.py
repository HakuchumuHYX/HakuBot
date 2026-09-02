from __future__ import annotations

import time
from typing import Any

import aiohttp
from nonebot import require
from nonebot.adapters.onebot.v11 import Bot
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_fixed

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler as apscheduler

from ..utils.network import (
    DEFAULT_TIMEOUT,
    INSECURE_SSL,
    HttpError,
    get_client_session,
    get_effective_proxy,
)
from ..utils.tools import get_exc_desc, get_logger
from .config import ARTICLE_DIR, DEFAULT_USER_AGENT, plugin_config
from .deliver import deliver_pending, get_group_bot_map, send_debug
from .parser import (
    build_raw_document,
    current_seen,
    event_id_for,
    now_iso,
    parse_date,
    parse_feed,
)
from .render import render_document
from .store import (
    abandon_pending,
    load_state,
    mark_pending_alerted,
    mark_pending_failure,
    pending_expired,
    pending_images_missing,
    pending_keep_names,
    save_json,
    save_state,
)
from .summary import summarize_directory

logger = get_logger("juya_daily_fetcher.scheduler")
POLL_JOB_ID = "juya_daily_fetcher_poll"
CLEANUP_JOB_ID = "juya_daily_fetcher_cleanup"


def _should_retry_feed(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return exc.status_code >= 500
    return isinstance(exc, (aiohttp.ClientError, TimeoutError, OSError))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception(_should_retry_feed),
    reraise=True,
)
async def fetch_feed(feed_url: str, state: dict[str, Any]) -> tuple[str, bytes | None, dict[str, str]]:
    headers = {
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if state.get("etag"):
        headers["If-None-Match"] = str(state["etag"])
    if state.get("last_modified"):
        headers["If-Modified-Since"] = str(state["last_modified"])
    async with get_client_session().get(
        feed_url,
        headers=headers,
        proxy=get_effective_proxy(),
        verify_ssl=not INSECURE_SSL,
        timeout=DEFAULT_TIMEOUT,
    ) as response:
        if response.status == 304:
            return "not_modified", None, {}
        if response.status >= 400:
            raise HttpError(response.status, response.reason or "")
        return "updated", await response.read(), {
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
        }


async def _alert_pending_stuck(pending: dict[str, Any]) -> None:
    text = (
        "【橘鸦 AI 早报】投递持续失败，将继续重试且不会把这期标成已读。\n"
        f"失败次数：{pending.get('fail_count') or 0}\n"
        f"最近错误：{pending.get('last_error') or '无'}"
    )
    bots_by_group = await get_group_bot_map()
    bot: Bot | None = next(iter(bots_by_group.values()), None)
    if bot is None:
        from nonebot import get_bot
        try:
            maybe_bot = get_bot()
        except (ValueError, KeyError):
            logger.warning("早报投递失败告警暂时无法发送（无可用 Bot 连接）")
            return
        bot = maybe_bot if isinstance(maybe_bot, Bot) else None
    if bot is None:
        logger.warning("早报投递失败告警暂时无法发送（无 OneBot v11 连接）")
        return

    user_ids: list[str] = []
    if plugin_config.debug_user_id:
        user_ids.append(plugin_config.debug_user_id)
    else:
        user_ids.extend(str(item) for item in (getattr(bot.config, "superusers", None) or []))
    if not user_ids:
        logger.warning("早报投递持续失败，但未配置 debug_user_id / superusers，无法告警")
        return

    for user_id in user_ids:
        try:
            await bot.send_private_msg(user_id=int(user_id), message=text)
        except Exception as exc:
            logger.warning(f"向 {user_id} 发送早报投递失败告警失败: {get_exc_desc(exc)}")


async def _prepare_and_queue(state: dict[str, Any], items: list[dict[str, Any]], updates: list[dict[str, Any]]) -> None:
    updates.sort(key=lambda item: parse_date(str(item["pub_date"])))
    event_id = event_id_for(updates)
    document = build_raw_document(plugin_config.feed_url, updates)
    json_path = ARTICLE_DIR / f"{event_id}.json"
    image_paths = await render_document(document, event_id)
    summary = await summarize_directory(document["directory"], document["date"])
    document["summary"] = summary
    save_json(json_path, document)
    logger.info(f"已解析并渲染早报，共 {len(image_paths)} 页")
    state["pending"] = {
        "kind": "rss_fanout",
        "event_id": event_id,
        "json_path": str(json_path),
        "summary": summary,
        "image_paths": [str(path) for path in image_paths],
        "source_url": str(document.get("source_url") or ""),
        "targets": {
            target: {"summary_sent": False, "report_sent": False}
            for target in plugin_config.targets
        },
        "seen_after": current_seen(items),
        "created_at": now_iso(),
        "fail_count": 0,
    }
    save_state(state)


async def _deliver_or_record_failure(state: dict[str, Any]) -> str:
    bots_by_group = await get_group_bot_map()
    if not bots_by_group:
        logger.info("当前没有可用的 Bot 连接，跳过本轮早报投递")
        return "no_bot"
    try:
        await deliver_pending(state, bots_by_group=bots_by_group)
    except Exception as exc:
        mark_pending_failure(state, get_exc_desc(exc))
        logger.exception(f"早报投递失败: {get_exc_desc(exc)}")
        return "deliver_failed"
    return "delivered_pending" if state.get("pending") else "delivered"


async def run_once(*, force_latest: bool = False) -> str:
    state = load_state()
    pending = state.get("pending")
    if isinstance(pending, dict):
        if pending_images_missing(pending):
            logger.error("pending 图片已缺失，丢弃 pending 但不推进已读，下一轮将重新渲染")
            abandon_pending(state)
            state = load_state()
        else:
            if pending_expired(pending) and not pending.get("alerted_at"):
                logger.warning(
                    "早报 pending 已超过失败阈值，将告警并继续重试 "
                    f"fail_count={pending.get('fail_count')} last_error={pending.get('last_error')}"
                )
                await _alert_pending_stuck(pending)
                mark_pending_alerted(state)
            return await _deliver_or_record_failure(state)

    if not plugin_config.targets and not force_latest:
        logger.info("未配置 targets，跳过本轮早报轮询")
        return "no_targets"

    try:
        fetch_status, feed_bytes, headers = await fetch_feed(
            plugin_config.feed_url,
            {} if force_latest else state,
        )
    except Exception as exc:
        logger.exception(f"拉取早报 RSS 失败: {get_exc_desc(exc)}")
        return "fetch_failed"

    if fetch_status == "not_modified":
        logger.info("RSS 未更新（HTTP 304）")
        return "not_modified"
    if feed_bytes is None:
        logger.error("RSS 返回空内容")
        return "fetch_failed"

    try:
        items = parse_feed(feed_bytes)
    except Exception as exc:
        logger.exception(f"解析早报 RSS 失败: {get_exc_desc(exc)}")
        return "parse_failed"

    if not force_latest:
        state.update({
            "feed_url": plugin_config.feed_url,
            "etag": headers.get("etag", state.get("etag", "")),
            "last_modified": headers.get("last_modified", state.get("last_modified", "")),
            "last_checked_at": now_iso(),
        })

    if force_latest:
        updates = [dict(items[0], change_kind="manual")]
    else:
        if not state.get("initialized"):
            state.update({
                "initialized": True,
                "seen": current_seen(items),
                "initialized_at": now_iso(),
            })
            save_state(state)
            logger.info(f"已建立基线（{len(items)} 条），本轮不推送历史早报")
            return "initialized"
        seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
        updates = []
        for item in items:
            old = seen.get(item["guid"])
            if not isinstance(old, dict):
                updates.append(dict(item, change_kind="new"))
            elif old.get("fingerprint") != item["fingerprint"]:
                updates.append(dict(item, change_kind="changed"))
        if not updates:
            state["seen"] = current_seen(items)
            save_state(state)
            logger.info(f"早报无更新（共检查 {len(items)} 条）")
            return "no_updates"

    try:
        await _prepare_and_queue(state, items, updates)
    except Exception as exc:
        logger.exception(f"渲染早报失败: {get_exc_desc(exc)}")
        return "render_failed"

    result = await _deliver_or_record_failure(state)
    if result == "delivered":
        logger.info(f"已向 {len(plugin_config.targets)} 个群投递早报")
    return result


async def run_debug(bot: Bot) -> str:
    fetch_status, feed_bytes, _headers = await fetch_feed(plugin_config.feed_url, {})
    if feed_bytes is None:
        raise RuntimeError("RSS 返回空内容")
    items = parse_feed(feed_bytes)
    item = dict(items[0], change_kind="debug")
    document = build_raw_document(plugin_config.feed_url, [item])
    event_id = f"debug-{event_id_for([item])}"
    image_paths = await render_document(document, event_id)
    summary = await summarize_directory(document["directory"], document["date"])
    document["summary"] = summary
    save_json(ARTICLE_DIR / f"{event_id}.json", document)
    await send_debug(bot, plugin_config.debug_user_id, summary, image_paths)
    return f"已向 {plugin_config.debug_user_id} 发送 {len(image_paths)} 页测试早报"


async def poll_juya_daily() -> None:
    try:
        result = await run_once()
        logger.info(f"橘鸦早报轮询结束: {result}")
    except Exception as exc:
        logger.exception(f"橘鸦早报轮询失败: {get_exc_desc(exc)}")


async def cleanup_articles() -> None:
    retention_days = max(1, int(plugin_config.article_retention_days or 7))
    cutoff = time.time() - retention_days * 86400
    pending = load_state().get("pending")
    keep = pending_keep_names(pending if isinstance(pending, dict) else None)
    if not ARTICLE_DIR.exists():
        return
    removed = 0
    for path in ARTICLE_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".png", ".json"}:
            continue
        if path.name in keep:
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning(f"清理早报产物失败 {path}: {exc}")
    if removed:
        logger.info(f"已清理 {removed} 个过期早报产物")


def _register_jobs() -> None:
    interval = max(1, int(plugin_config.poll_interval_minutes or 15))
    try:
        apscheduler.add_job(
            poll_juya_daily,
            trigger="interval",
            minutes=interval,
            jitter=30,
            id=POLL_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        apscheduler.add_job(
            cleanup_articles,
            trigger="cron",
            hour=4,
            minute=20,
            id=CLEANUP_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    except (ValueError, TypeError, LookupError) as exc:
        logger.error(f"注册橘鸦早报定时任务失败: {get_exc_desc(exc)}")
        return
    logger.info(f"橘鸦早报轮询任务已注册：间隔 {interval} 分钟")


_register_jobs()
