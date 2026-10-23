"""写库：一个后台线程独占连接，各处只往队列里放 (sql, 参数)

用线程和 queue.Queue，是因为 loguru sink 可能在任意线程里被调用（比如 to_thread 里打的 warning）。
等锁交给 busy_timeout；单行写失败只记 warning，丢掉这行继续。
"""

import queue
import sqlite3
import threading
from pathlib import Path
from urllib.parse import quote

from loguru import logger

INSERT_RESPONSE = """
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

INSERT_DIAGNOSTIC = """
    INSERT INTO diagnostic_logs (
        run_id, created_at_ms, level, logger_name, module_name,
        plugin_name, message_summary, full_log_gzip, raw_sha256
    ) VALUES (
        :run_id, :created_at_ms, :level, :logger_name, :module_name,
        :plugin_name, :message_summary, :full_log_gzip, :raw_sha256
    )
"""

UPSERT_STATUS = """
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
        collector_heartbeat_at_ms = excluded.collector_heartbeat_at_ms,
        updated_at_ms = excluded.updated_at_ms
"""

_STOP = object()
_queue: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
# 带这个标记的日志 sink 不收，免得写库失败的 warning 又要写库
_internal_logger = logger.bind(webconsole_bridge_internal=True)


def open_database(path: Path) -> None:
    """表由 webconsole 建；库打不开直接抛，插件加载失败"""
    global _thread
    connection = sqlite3.connect(
        f"file:{quote(str(path), safe='/')}?mode=rw",
        uri=True,
        timeout=5,
        check_same_thread=False,
    )
    connection.execute("PRAGMA synchronous = NORMAL")
    _thread = threading.Thread(
        target=_write_loop, args=(connection,), name="webconsole-writer", daemon=True
    )
    _thread.start()


def put(sql: str, row: dict) -> None:
    _queue.put((sql, row))


def close() -> None:
    _queue.put(_STOP)
    _thread.join(timeout=5)


def _write_loop(connection: sqlite3.Connection) -> None:
    while (item := _queue.get()) is not _STOP:
        sql, row = item
        try:
            with connection:
                connection.execute(sql, row)
        except sqlite3.Error as e:
            _internal_logger.warning(f"WebConsole 写库失败: {type(e).__name__}: {e}")
    connection.close()
