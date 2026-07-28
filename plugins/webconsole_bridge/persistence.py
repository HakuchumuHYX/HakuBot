from __future__ import annotations

import asyncio
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.parse import quote

import aiosqlite
from loguru import logger

from .capture import CompletedResponse, DiagnosticLogEntry, RunContext
from .config import BridgeConfig
from .status import BotStatusSnapshot

SUCCESS_QUEUE_SIZE = 5_000


def _encode_full_value(
    value: str | None,
) -> tuple[bytes | None, str | None]:
    if value is None:
        return None, None
    raw = value.encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0), hashlib.sha256(
        raw
    ).hexdigest()


def _response_payload(response: CompletedResponse) -> dict[str, Any]:
    context = response.context
    return {
        "run_id": context.run_id,
        "started_at_ms": context.started_at_ms,
        "finished_at_ms": context.finished_at_ms,
        "duration_ms": context.duration_ms,
        "status": response.status,
        "plugin_name": context.plugin_name,
        "plugin_id": context.plugin_id,
        "module_name": context.module_name,
        "matcher_type": context.matcher_type,
        "matcher_lineno": context.matcher_lineno,
        "bot_id": context.bot_id,
        "event_name": context.event_name,
        "group_id": context.group_id,
        "user_id": context.user_id,
        "source_message_id": context.source_message_id,
        "request_summary": context.request_summary,
        "response_summary": response.response_summary,
        "send_count": response.send_count,
        "send_success_count": response.send_success_count,
        "send_failure_count": response.send_failure_count,
        "max_log_level": response.max_log_level,
        "error_type": response.error_type,
        "error_message": response.error_message,
        "has_full_diagnostics": response.has_full_diagnostics,
        "request_raw": response.request_raw,
        "response_raw": response.response_raw,
        "logs_raw": response.logs_raw,
        "created_at_ms": time.time_ns() // 1_000_000,
    }


def _write_spool_atomic(
    spool_path: Path,
    payload: dict[str, Any],
) -> Path:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".webconsole-spool-",
        suffix=".tmp",
        dir=spool_path,
    )
    target = spool_path / (
        f"{int(time.time_ns())}-{payload.get('run_id', 'diagnostic')}.json.gz"
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(compressed)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
        directory_fd = os.open(spool_path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return target


def _write_stderr(message: str) -> None:
    try:
        os.write(2, (message.rstrip() + "\n").encode("utf-8", errors="replace"))
    except OSError:
        pass


def _read_spool(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as spool_file:
        payload = json.load(spool_file)
    if not isinstance(payload, dict):
        raise ValueError("spool payload is not an object")
    return payload


class PersistenceWriter:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._connection: aiosqlite.Connection | None = None
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(
            maxsize=SUCCESS_QUEUE_SIZE
        )
        self._pending_status: dict[str, dict[str, Any]] = {}
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False
        self._overflow_active = False
        self._dropped_success_count = 0

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        database_uri = (
            f"file:{quote(str(self.config.database_path), safe='/')}?mode=rw"
        )
        connection = await aiosqlite.connect(database_uri, uri=True)
        try:
            await connection.execute("PRAGMA busy_timeout = 5000")
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA synchronous = NORMAL")
        except BaseException:
            await connection.close()
            raise
        self._connection = connection
        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name="webconsole-database-writer",
        )

    async def stop(self) -> None:
        if not self._running and self._connection is None:
            return
        self._running = False
        task = self._worker_task
        self._worker_task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        connection = self._connection
        self._connection = None
        if connection is not None:
            await connection.close()

    async def persist_capture(
        self,
        context: RunContext,
        response: CompletedResponse | None,
    ) -> None:
        if response is None:
            return
        if response.has_full_diagnostics:
            try:
                path = await asyncio.to_thread(
                    _write_spool_atomic,
                    self.config.spool_path,
                    {
                        "kind": "response",
                        "run_id": context.run_id,
                        "response": _response_payload(response),
                    },
                )
            except Exception as exc:
                logger.bind(webconsole_bridge_internal=True).critical(
                    "WebConsole bridge could not persist complete diagnostic "
                    f"spool for run {context.run_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                return
            if self._running:
                try:
                    self._queue.put_nowait(("spool", path))
                except asyncio.QueueFull:
                    pass
            return

        if not self._running:
            return
        try:
            self._queue.put_nowait(("response", _response_payload(response)))
            if self._overflow_active:
                logger.bind(webconsole_bridge_internal=True).info(
                    "WebConsole bridge success queue recovered after dropping "
                    f"{self._dropped_success_count} normal success summaries"
                )
                self._overflow_active = False
        except asyncio.QueueFull:
            self._dropped_success_count += 1
            if not self._overflow_active:
                self._overflow_active = True
                logger.bind(webconsole_bridge_internal=True).warning(
                    "WebConsole bridge success queue is full; normal success "
                    "summaries will be dropped until the writer recovers "
                    f"(dropped={self._dropped_success_count})"
                )

    async def persist_status(self, snapshot: BotStatusSnapshot) -> None:
        if not self._running:
            return
        self._pending_status[snapshot.bot_id] = asdict(snapshot)
        try:
            self._queue.put_nowait(("wake", None))
        except asyncio.QueueFull:
            pass

    def persist_log_from_sink(self, entry: DiagnosticLogEntry) -> None:
        try:
            _write_spool_atomic(
                self.config.spool_path,
                {
                    "kind": "diagnostic",
                    "run_id": entry.run_id,
                    "diagnostic": asdict(entry),
                },
            )
        except Exception as exc:
            _write_stderr(
                "WebConsole bridge could not persist diagnostic spool: "
                f"{type(exc).__name__}: {exc}"
            )

    async def flush(self, timeout: float = 5) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            spool_files = tuple(self.config.spool_path.glob("*.json.gz"))
            if (
                self._queue.empty()
                and not self._pending_status
                and not spool_files
            ):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("persistence writer did not flush in time")
            await asyncio.sleep(0.01)

    async def _worker_loop(self) -> None:
        while self._running or not self._queue.empty():
            command: tuple[str, Any] | None = None
            try:
                command = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=0.25,
                )
            except asyncio.TimeoutError:
                pass

            if command is not None:
                kind, payload = command
                try:
                    if kind == "response":
                        await self._insert_response(payload)
                    elif kind == "spool":
                        await self._import_spool(Path(payload))
                except Exception as exc:
                    logger.bind(webconsole_bridge_internal=True).warning(
                        "WebConsole bridge database writer failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                finally:
                    self._queue.task_done()

            try:
                await self._flush_pending_status()
            except Exception as exc:
                logger.bind(webconsole_bridge_internal=True).warning(
                    "WebConsole bridge could not update current Bot status: "
                    f"{type(exc).__name__}: {exc}"
                )
            await self._scan_spool(limit=10)

        try:
            await self._flush_pending_status()
        except Exception as exc:
            logger.bind(webconsole_bridge_internal=True).warning(
                "WebConsole bridge could not flush current Bot status during "
                f"shutdown: {type(exc).__name__}: {exc}"
            )
        await self._scan_spool(limit=None)

    async def _flush_pending_status(self) -> None:
        if not self._pending_status:
            return
        pending = self._pending_status
        self._pending_status = {}
        for bot_id, payload in pending.items():
            try:
                await self._upsert_status(payload)
            except Exception:
                self._pending_status[bot_id] = payload
                raise

    async def _scan_spool(self, *, limit: int | None) -> None:
        paths = sorted(self.config.spool_path.glob("*.json.gz"))
        if limit is not None:
            paths = paths[:limit]
        for path in paths:
            try:
                await self._import_spool(path)
            except Exception as exc:
                logger.bind(webconsole_bridge_internal=True).warning(
                    "WebConsole bridge could not import durable spool "
                    f"{path.name}: {type(exc).__name__}: {exc}"
                )
                break

    async def _import_spool(self, path: Path) -> None:
        if not path.exists():
            return
        payload = await asyncio.to_thread(_read_spool, path)
        kind = payload.get("kind")
        if kind == "response":
            response = payload.get("response")
            if not isinstance(response, dict):
                raise ValueError("response spool is missing response object")
            await self._insert_response(response)
        elif kind == "diagnostic":
            diagnostic = payload.get("diagnostic")
            if not isinstance(diagnostic, dict):
                raise ValueError(
                    "diagnostic spool is missing diagnostic object"
                )
            await self._insert_diagnostic(diagnostic)
        else:
            raise ValueError(f"unsupported spool kind: {kind!r}")
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def _insert_response(self, payload: dict[str, Any]) -> None:
        connection = self._require_connection()
        request_gzip, request_sha = _encode_full_value(
            payload.get("request_raw")
        )
        response_gzip, response_sha = _encode_full_value(
            payload.get("response_raw")
        )
        logs_gzip, logs_sha = _encode_full_value(payload.get("logs_raw"))
        parameters = {
            **payload,
            "has_full_diagnostics": int(
                bool(payload["has_full_diagnostics"])
            ),
            "request_raw_gzip": request_gzip,
            "response_raw_gzip": response_gzip,
            "logs_raw_gzip": logs_gzip,
            "request_raw_sha256": request_sha,
            "response_raw_sha256": response_sha,
            "logs_raw_sha256": logs_sha,
        }
        sql = """
            INSERT OR IGNORE INTO response_events (
                run_id, started_at_ms, finished_at_ms, duration_ms,
                status, plugin_name, plugin_id, module_name, matcher_type,
                matcher_lineno, bot_id, event_name, group_id, user_id,
                source_message_id, request_summary, response_summary,
                send_count, send_success_count, send_failure_count,
                max_log_level, error_type, error_message,
                has_full_diagnostics, request_raw_gzip, response_raw_gzip,
                logs_raw_gzip, request_raw_sha256, response_raw_sha256,
                logs_raw_sha256, created_at_ms
            ) VALUES (
                :run_id, :started_at_ms, :finished_at_ms, :duration_ms,
                :status, :plugin_name, :plugin_id, :module_name, :matcher_type,
                :matcher_lineno, :bot_id, :event_name, :group_id, :user_id,
                :source_message_id, :request_summary, :response_summary,
                :send_count, :send_success_count, :send_failure_count,
                :max_log_level, :error_type, :error_message,
                :has_full_diagnostics, :request_raw_gzip, :response_raw_gzip,
                :logs_raw_gzip, :request_raw_sha256, :response_raw_sha256,
                :logs_raw_sha256, :created_at_ms
            )
        """
        await self._execute_commit(sql, parameters)

    async def _insert_diagnostic(self, payload: dict[str, Any]) -> None:
        full_log = str(payload["full_log"])
        full_log_gzip, raw_sha256 = _encode_full_value(full_log)
        parameters = {
            "run_id": payload.get("run_id"),
            "created_at_ms": int(payload["created_at_ms"]),
            "level": str(payload["level"]),
            "logger_name": payload.get("logger_name"),
            "module_name": payload.get("module_name"),
            "plugin_name": payload.get("plugin_name"),
            "message_summary": str(payload.get("message", ""))[:2_000],
            "full_log_gzip": full_log_gzip,
            "raw_sha256": raw_sha256,
        }
        sql = """
            INSERT INTO diagnostic_logs (
                run_id, created_at_ms, level, logger_name, module_name,
                plugin_name, message_summary, full_log_gzip, raw_sha256
            ) VALUES (
                :run_id, :created_at_ms, :level, :logger_name, :module_name,
                :plugin_name, :message_summary, :full_log_gzip, :raw_sha256
            )
        """
        await self._execute_commit(sql, parameters)

    async def _upsert_status(self, payload: dict[str, Any]) -> None:
        parameters = {
            **payload,
            "connected": int(bool(payload["connected"])),
            "heartbeat_online": (
                None
                if payload["heartbeat_online"] is None
                else int(bool(payload["heartbeat_online"]))
            ),
            "heartbeat_good": (
                None
                if payload["heartbeat_good"] is None
                else int(bool(payload["heartbeat_good"]))
            ),
        }
        sql = """
            INSERT INTO bot_status (
                bot_id, adapter, connected, connection_started_at_ms,
                last_heartbeat_at_ms, heartbeat_online, heartbeat_good,
                collector_heartbeat_at_ms, updated_at_ms
            ) VALUES (
                :bot_id, :adapter, :connected, :connection_started_at_ms,
                :last_heartbeat_at_ms, :heartbeat_online, :heartbeat_good,
                :collector_heartbeat_at_ms, :updated_at_ms
            )
            ON CONFLICT(bot_id) DO UPDATE SET
                adapter = excluded.adapter,
                connected = excluded.connected,
                connection_started_at_ms = excluded.connection_started_at_ms,
                last_heartbeat_at_ms = excluded.last_heartbeat_at_ms,
                heartbeat_online = excluded.heartbeat_online,
                heartbeat_good = excluded.heartbeat_good,
                collector_heartbeat_at_ms =
                    excluded.collector_heartbeat_at_ms,
                updated_at_ms = excluded.updated_at_ms
        """
        await self._execute_commit(sql, parameters)

    async def _execute_commit(
        self,
        sql: str,
        parameters: dict[str, Any],
    ) -> None:
        connection = self._require_connection()
        delays = (0.05, 0.1, 0.2, 0.4, 0.8)
        for attempt in range(len(delays) + 1):
            try:
                await connection.execute(sql, parameters)
                await connection.commit()
                return
            except aiosqlite.OperationalError as exc:
                await connection.rollback()
                message = str(exc).lower()
                if (
                    ("locked" not in message and "busy" not in message)
                    or attempt >= len(delays)
                ):
                    raise
                await asyncio.sleep(delays[attempt])
            except BaseException:
                await connection.rollback()
                raise

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("persistence database is not open")
        return self._connection
