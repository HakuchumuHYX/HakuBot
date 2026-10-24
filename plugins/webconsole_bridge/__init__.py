"""把 HakuBot 的响应、WARNING 起的日志和 Bot 在线状态写进 webconsole 的库，webconsole 只读不写

记录口径（什么算失败、什么留完整诊断）见 capture.py。
"""

from pathlib import Path
from typing import Any

from loguru import logger
from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import HeartbeatMetaEvent
from nonebot.matcher import Matcher, current_matcher
from nonebot.message import event_postprocessor, run_postprocessor, run_preprocessor
from pydantic import field_validator

from core.lifecycle import on_plugin_shutdown
from core.settings import ConfigModel, load_config
from plugins.webconsole_bridge import capture, store

PLUGIN_ID = "webconsole_bridge"
RUN_KEY = "_webconsole_bridge_run"


class Config(ConfigModel):
    # 与 webconsole 共用的库
    database_path: Path

    @field_validator("database_path")
    @classmethod
    def _require_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("must be an absolute path")
        return value


config = load_config(PLUGIN_ID, Config)
store.open_database(config.database_path)
driver = get_driver()


# ---- 响应 ----


@run_preprocessor
async def _begin_run(matcher: Matcher):
    matcher.state[RUN_KEY] = capture.Run()


@Bot.on_called_api
async def _record_api(
    bot: Bot, exception: Exception | None, api: str, data: dict, result: Any
):
    if api not in capture.RESPONSE_APIS:
        return
    # 别的 pre/postprocessor 里发的消息也带着 matcher 上下文，但不属于这次运行
    matcher = current_matcher.get(None)
    run = matcher.state.get(RUN_KEY) if matcher is not None else None
    if run is not None:
        run.api_calls.append(capture.api_call(api, data, result, exception))


@run_postprocessor
async def _finish_run(
    matcher: Matcher, exception: Exception | None, bot: Bot, event: Event
):
    run = matcher.state.pop(RUN_KEY)
    row = capture.response_row(run, matcher, event, exception)
    if row is not None:
        store.put(store.INSERT_RESPONSE, row)


# ---- 日志 ----


def _sink(message) -> None:
    record = message.record
    if record["extra"].get("webconsole_bridge_internal"):
        return
    matcher = current_matcher.get(None)
    run = matcher.state.get(RUN_KEY) if matcher is not None else None
    entry = capture.log_entry(record, str(message), run, matcher)
    if run is not None:
        run.logs.append(entry)
    store.put(store.INSERT_DIAGNOSTIC, capture.diagnostic_row(entry))


logger.add(
    _sink,
    level="WARNING",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} - {message}",
    diagnose=False,
    backtrace=True,
)


# ---- Bot 状态 ----
# webconsole 判在线看的是 NapCat 心跳（30 秒一次）是否 90 秒内到过；
# 心跳经 HakuBot 转写，所以 HakuBot 挂了也会过期

_status: dict[str, dict] = {}


def _write_status(bot_id: str) -> None:
    store.put(store.UPSERT_STATUS, dict(_status[bot_id]))


@driver.on_bot_connect
async def _bot_connect(bot: Bot):
    _status[bot.self_id] = {
        "bot_id": bot.self_id,
        "connected": 1,
        "connection_started_at_ms": capture.now_ms(),
        "last_heartbeat_at_ms": None,
        "heartbeat_online": None,
        "heartbeat_good": None,
    }
    _write_status(bot.self_id)


@driver.on_bot_disconnect
async def _bot_disconnect(bot: Bot):
    _status[bot.self_id]["connected"] = 0
    _write_status(bot.self_id)


@event_postprocessor
async def _heartbeat(bot: Bot, event: HeartbeatMetaEvent):
    _status[bot.self_id].update(
        last_heartbeat_at_ms=capture.now_ms(),
        heartbeat_online=int(event.status.online),
        heartbeat_good=int(event.status.good),
    )
    _write_status(bot.self_id)


@on_plugin_shutdown(driver, PLUGIN_ID)
def _stop():
    store.close()
