from __future__ import annotations

import asyncio
from typing import Any

import httpx
from nonebot import get_bot, require
from nonebot.adapters.onebot.v11 import Bot

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler as apscheduler

from ..utils.tools import get_exc_desc, get_logger
from .config import ARTICLE_DIR, plugin_config
from .deliver import deliver_pending
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
    mark_pending_failure,
    pending_expired,
    save_json,
    save_state,
)
from .summary import summarize_directory

logger = get_logger("juya_daily_fetcher.scheduler")
POLL_JOB_ID = "juya_daily_fetcher_poll"
_run_lock = asyncio.Lock()


async def fetch_feed(feed_url: str, state: dict[str, Any]) -> tuple[str, bytes | None, dict[str, str]]:
    headers = {
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        "User-Agent": "juya-daily-fetcher/1.0 (+hakubot)",
    }
    if state.get("etag"):
        headers["If-None-Match"] = str(state["etag"])
    if state.get("last_modified"):
        headers["If-Modified-Since"] = str(state["last_modified"])
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(feed_url, headers=headers)
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"feed request failed: {exc}") from exc
    if response.status_code == 304:
        return "not_modified", None, {}
    if response.status_code >= 400:
        raise RuntimeError(f"feed HTTP {response.status_code}")
    return "updated", response.content, {
        "etag": response.headers.get("ETag", ""),
        "last_modified": response.headers.get("Last-Modified", ""),
    }


def _get_bot() -> Bot:
    bot = get_bot()
    if not isinstance(bot, Bot):
        raise RuntimeError("no OneBot v11 bot is available")
    return bot


async def _prepare_and_queue(state: dict[str, Any], items: list[dict[str, Any]], updates: list[dict[str, Any]]) -> None:
    updates.sort(key=lambda item: parse_date(str(item["pub_date"])))
    event_id = event_id_for(updates)
    document = build_raw_document(plugin_config.feed_url, updates)
    json_path = ARTICLE_DIR / f"{event_id}.json"
    image_paths = await render_document(document, event_id)
    summary = await summarize_directory(document["directory"], document["date"])
    document["summary"] = summary
    save_json(json_path, document)
    logger.info(f"parsed original RSS once and rendered once into {len(image_paths)} image page(s)")
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


async def run_once(*, force_latest: bool = False) -> str:
    state = load_state()
    pending = state.get("pending")
    if isinstance(pending, dict):
        if pending_expired(pending):
            logger.error(
                "abandoning stale RSS fanout "
                f"fail_count={pending.get('fail_count')} last_error={pending.get('last_error')}"
            )
            abandon_pending(state)
            state = load_state()
        else:
            bot = _get_bot()
            try:
                await deliver_pending(bot, state)
            except Exception as exc:
                mark_pending_failure(state, get_exc_desc(exc))
                raise
            return "delivered_pending"

    fetch_status, feed_bytes, headers = await fetch_feed(
        plugin_config.feed_url,
        {} if force_latest else state,
    )
    if fetch_status == "not_modified":
        logger.info("RSS not modified (HTTP 304)")
        return "not_modified"
    if feed_bytes is None:
        raise RuntimeError("feed returned no XML")
    items = parse_feed(feed_bytes)

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
            logger.info(f"initialized baseline with {len(items)} RSS item(s); no notification sent")
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
            logger.info(f"no RSS updates ({len(items)} item(s) checked)")
            return "no_updates"

    await _prepare_and_queue(state, items, updates)
    bot = _get_bot()
    try:
        await deliver_pending(bot, state)
    except Exception as exc:
        mark_pending_failure(state, get_exc_desc(exc))
        raise
    logger.info(f"rendered original RSS once and delivered to {len(plugin_config.targets)} target(s)")
    return "delivered"


async def run_debug(bot: Bot) -> str:
    from .deliver import send_debug

    fetch_status, feed_bytes, _headers = await fetch_feed(plugin_config.feed_url, {})
    if feed_bytes is None:
        raise RuntimeError("RSS returned no body")
    items = parse_feed(feed_bytes)
    item = dict(items[0], change_kind="debug")
    document = build_raw_document(plugin_config.feed_url, [item])
    event_id = f"debug-{event_id_for([item])}"
    image_paths = await render_document(document, event_id)
    summary = await summarize_directory(document["directory"], document["date"])
    document["summary"] = summary
    save_json(ARTICLE_DIR / f"{event_id}.json", document)
    await send_debug(bot, plugin_config.debug_user_id, summary, image_paths)
    return f"debug delivered {len(image_paths)} page(s) to {plugin_config.debug_user_id}"


@apscheduler.scheduled_job(
    "interval",
    minutes=plugin_config.poll_interval_minutes,
    id=POLL_JOB_ID,
    max_instances=1,
    coalesce=True,
    jitter=30,
)
async def poll_juya_daily() -> None:
    if _run_lock.locked():
        logger.info("another RSS watcher run is active; exiting")
        return
    async with _run_lock:
        try:
            result = await run_once()
            logger.info(f"juya daily poll finished: {result}")
        except Exception as exc:
            logger.exception(f"RSS watcher failed: {get_exc_desc(exc)}")
