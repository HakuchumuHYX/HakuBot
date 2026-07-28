from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
import time
from typing import Any

from loguru import logger
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import HeartbeatMetaEvent
from nonebot.message import event_postprocessor

from .config import BridgeConfig
from .database import probe_storage
from .models import BridgeState, ProbeResult

ProbeFunction = Callable[[BridgeConfig], Awaitable[ProbeResult]]
LogFunction = Callable[[str, str], None]
StatusWriteCallback = Callable[["BotStatusSnapshot"], Awaitable[None]]
AvailabilityCallback = Callable[[bool], Awaitable[None]]


def _default_log(level: str, message: str) -> None:
    logger.bind(webconsole_bridge_internal=True).log(level, message)


class BridgeRuntime:
    def __init__(
        self,
        config: BridgeConfig,
        *,
        probe: ProbeFunction = probe_storage,
        emit_log: LogFunction = _default_log,
    ) -> None:
        self.config = config
        self.state = BridgeState.DISABLED
        self.last_unavailable_reason = ""
        self._probe = probe
        self._emit_log = emit_log
        self._probe_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._availability_callback: AvailabilityCallback | None = None

    @property
    def available(self) -> bool:
        return self.state is BridgeState.AVAILABLE

    def set_availability_callback(
        self,
        callback: AvailabilityCallback | None,
    ) -> None:
        self._availability_callback = callback

    async def start(self) -> None:
        if not self.config.enabled:
            self.state = BridgeState.DISABLED
            return

        self._stop_event.clear()
        await self.check_now(initial=True)
        self._probe_task = asyncio.create_task(
            self._probe_loop(),
            name="webconsole-bridge-probe",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._probe_task
        self._probe_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self.available and self._availability_callback is not None:
            await self._availability_callback(False)
        self.state = BridgeState.STOPPED

    async def check_now(self, *, initial: bool = False) -> ProbeResult:
        result = await self._probe(self.config)
        previous = self.state

        if result.available:
            self.state = BridgeState.AVAILABLE
            self.last_unavailable_reason = ""
            if (
                previous is not BridgeState.AVAILABLE
                and self._availability_callback is not None
            ):
                try:
                    await self._availability_callback(True)
                except Exception as exc:
                    self.state = BridgeState.DISABLED_UNAVAILABLE
                    self.last_unavailable_reason = (
                        "persistence startup failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    if initial or previous is BridgeState.AVAILABLE:
                        self._emit_log(
                            "WARNING",
                            "WebConsole bridge persistence could not start; "
                            "event collection remains disabled: "
                            f"{self.last_unavailable_reason}",
                        )
                    return ProbeResult.failure(self.last_unavailable_reason)
            if previous is BridgeState.DISABLED_UNAVAILABLE:
                self._emit_log(
                    "INFO",
                    "WebConsole bridge storage is available again; "
                    "event collection has resumed (events during downtime "
                    "will not be backfilled)",
                )
            return result

        self.state = BridgeState.DISABLED_UNAVAILABLE
        self.last_unavailable_reason = result.reason
        if (
            previous is BridgeState.AVAILABLE
            and self._availability_callback is not None
        ):
            await self._availability_callback(False)
        if initial or previous is BridgeState.AVAILABLE:
            self._emit_log(
                "WARNING",
                "WebConsole bridge is unavailable and event collection is "
                f"disabled: {result.reason}. The bridge will retry silently "
                f"every {self.config.probe_interval_seconds:g} seconds",
            )
        return result

    async def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.probe_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                pass

            try:
                await self.check_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                previous = self.state
                self.state = BridgeState.DISABLED_UNAVAILABLE
                self.last_unavailable_reason = (
                    f"unexpected probe error: {type(exc).__name__}: {exc}"
                )
                if previous is BridgeState.AVAILABLE:
                    self._emit_log(
                        "WARNING",
                        "WebConsole bridge became unavailable; event "
                        f"collection is disabled: {self.last_unavailable_reason}",
                    )


@dataclass(slots=True)
class BotStatusSnapshot:
    bot_id: str
    adapter: str
    connected: bool
    connection_started_at_ms: int | None
    last_heartbeat_at_ms: int | None
    heartbeat_online: bool | None
    heartbeat_good: bool | None
    collector_heartbeat_at_ms: int
    updated_at_ms: int

    def is_online(self, now_ms: int) -> bool:
        collector_fresh = now_ms - self.collector_heartbeat_at_ms <= 45_000
        heartbeat_fresh = (
            self.last_heartbeat_at_ms is not None
            and now_ms - self.last_heartbeat_at_ms <= 90_000
        )
        heartbeat_grace = (
            self.last_heartbeat_at_ms is None
            and self.connection_started_at_ms is not None
            and now_ms - self.connection_started_at_ms <= 90_000
        )
        heartbeat_healthy = heartbeat_grace or (
            heartbeat_fresh
            and self.heartbeat_online is True
            and self.heartbeat_good is True
        )
        return self.connected and collector_fresh and heartbeat_healthy


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _adapter_name(bot: Bot) -> str:
    try:
        return str(bot.adapter.get_name())
    except Exception:
        return type(bot.adapter).__name__


class BotStatusManager:
    def __init__(
        self,
        *,
        write_callback: StatusWriteCallback | None = None,
        collector_interval_seconds: float = 15,
    ) -> None:
        self.snapshots: dict[str, BotStatusSnapshot] = {}
        self._write_callback = write_callback
        self._collector_interval_seconds = collector_interval_seconds
        self._collector_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def set_write_callback(
        self,
        callback: StatusWriteCallback | None,
    ) -> None:
        self._write_callback = callback

    async def connect(self, bot: Bot, *, now_ms: int | None = None) -> None:
        current_ms = _now_ms() if now_ms is None else now_ms
        bot_id = str(bot.self_id)
        snapshot = BotStatusSnapshot(
            bot_id=bot_id,
            adapter=_adapter_name(bot),
            connected=True,
            connection_started_at_ms=current_ms,
            last_heartbeat_at_ms=None,
            heartbeat_online=None,
            heartbeat_good=None,
            collector_heartbeat_at_ms=current_ms,
            updated_at_ms=current_ms,
        )
        self.snapshots[bot_id] = snapshot
        await self._write(snapshot)

    async def disconnect(
        self,
        bot: Bot,
        *,
        now_ms: int | None = None,
    ) -> None:
        current_ms = _now_ms() if now_ms is None else now_ms
        bot_id = str(bot.self_id)
        snapshot = self.snapshots.get(bot_id)
        if snapshot is None:
            snapshot = BotStatusSnapshot(
                bot_id=bot_id,
                adapter=_adapter_name(bot),
                connected=False,
                connection_started_at_ms=None,
                last_heartbeat_at_ms=None,
                heartbeat_online=None,
                heartbeat_good=None,
                collector_heartbeat_at_ms=current_ms,
                updated_at_ms=current_ms,
            )
            self.snapshots[bot_id] = snapshot
        else:
            snapshot.connected = False
            snapshot.updated_at_ms = current_ms
            snapshot.collector_heartbeat_at_ms = current_ms
        await self._write(snapshot)

    async def heartbeat(
        self,
        bot: Bot,
        event: HeartbeatMetaEvent,
        *,
        now_ms: int | None = None,
    ) -> None:
        current_ms = _now_ms() if now_ms is None else now_ms
        bot_id = str(bot.self_id)
        snapshot = self.snapshots.get(bot_id)
        if snapshot is None:
            snapshot = BotStatusSnapshot(
                bot_id=bot_id,
                adapter=_adapter_name(bot),
                connected=True,
                connection_started_at_ms=current_ms,
                last_heartbeat_at_ms=current_ms,
                heartbeat_online=bool(event.status.online),
                heartbeat_good=bool(event.status.good),
                collector_heartbeat_at_ms=current_ms,
                updated_at_ms=current_ms,
            )
            self.snapshots[bot_id] = snapshot
        else:
            snapshot.last_heartbeat_at_ms = current_ms
            snapshot.heartbeat_online = bool(event.status.online)
            snapshot.heartbeat_good = bool(event.status.good)
            snapshot.collector_heartbeat_at_ms = current_ms
            snapshot.updated_at_ms = current_ms
        await self._write(snapshot)

    async def refresh_collector_heartbeat(
        self,
        *,
        now_ms: int | None = None,
    ) -> None:
        current_ms = _now_ms() if now_ms is None else now_ms
        for snapshot in tuple(self.snapshots.values()):
            snapshot.collector_heartbeat_at_ms = current_ms
            snapshot.updated_at_ms = current_ms
            await self._write(snapshot)

    async def start(self) -> None:
        self._stop_event.clear()
        self._collector_task = asyncio.create_task(
            self._collector_loop(),
            name="webconsole-collector-heartbeat",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._collector_task
        self._collector_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _write(self, snapshot: BotStatusSnapshot) -> None:
        if self._write_callback is not None:
            await self._write_callback(snapshot)

    async def _collector_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._collector_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                await self.refresh_collector_heartbeat()


bot_status_manager = BotStatusManager()
_status_hooks_registered = False


def register_bot_status_hooks(
    driver: Any,
    runtime: BridgeRuntime,
) -> None:
    global _status_hooks_registered
    if _status_hooks_registered:
        return
    _status_hooks_registered = True

    @driver.on_bot_connect
    async def _webconsole_bot_connect(bot: Bot) -> None:
        await bot_status_manager.connect(bot)

    @driver.on_bot_disconnect
    async def _webconsole_bot_disconnect(bot: Bot) -> None:
        await bot_status_manager.disconnect(bot)

    @event_postprocessor
    async def _webconsole_heartbeat(
        bot: Bot,
        event: Event,
    ) -> None:
        if isinstance(event, HeartbeatMetaEvent):
            await bot_status_manager.heartbeat(bot, event)
