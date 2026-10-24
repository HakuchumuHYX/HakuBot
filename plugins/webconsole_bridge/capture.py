"""把一次 matcher 运行汇总成 response_events 的一行，把一条日志转成 diagnostic_logs 的一行

只记有发送、抛了异常或出现 ERROR 日志的运行；失败或带 WARNING 的附完整输入、输出和日志。
列的含义和约束以 webconsole 的 internal/database/schema.sql 为准。
"""

import base64
import gzip
import json
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from nonebot.adapters import Event
from nonebot.matcher import Matcher

RESPONSE_APIS = frozenset(
    {
        "send_msg",
        "send_group_msg",
        "send_private_msg",
        "send_group_forward_msg",
        "send_private_forward_msg",
    }
)
SUMMARY_LIMIT = 2_000
WARNING_NO = logger.level("WARNING").no
ERROR_NO = logger.level("ERROR").no


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class Run:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at_ms: int = field(default_factory=now_ms)
    started_ns: int = field(default_factory=time.monotonic_ns)
    api_calls: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)


def _traceback(exception: BaseException) -> str:
    return "".join(
        traceback.format_exception(type(exception), exception, exception.__traceback__)
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return str(value)
    if callable(getattr(value, "model_dump", None)):
        return value.model_dump(mode="json")
    if hasattr(value, "type") and hasattr(value, "data"):
        return {"type": str(value.type), "data": value.data}
    return repr(value)


def _dumps(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=_json_default
    )


def _truncate(value: str) -> str:
    return value if len(value) <= SUMMARY_LIMIT else value[:SUMMARY_LIMIT] + "…"


def _message_summary(value: Any) -> str:
    """文本段取原文，其它段写成 [image] 这样"""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return "[二进制]"
    if isinstance(value, (list, tuple)):
        return "".join(_message_summary(item) for item in value)
    if isinstance(value, dict) and "type" in value and "data" in value:
        segment_type, data = value["type"], value["data"]
    elif hasattr(value, "type") and hasattr(value, "data"):
        segment_type, data = value.type, value.data
    elif isinstance(value, dict):
        # 发送 API 的参数：普通消息取 message，合并转发只有 messages，写成 [forward]
        return _message_summary(value["message"]) if "message" in value else "[forward]"
    else:
        return str(value)
    if segment_type == "text" and isinstance(data, dict):
        return str(data.get("text", ""))
    return f"[{segment_type}]"


def _gzip(value: str | None) -> bytes | None:
    if value is None:
        return None
    return gzip.compress(value.encode("utf-8"), compresslevel=6, mtime=0)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def api_call(api: str, data: dict, result: Any, exception: Exception | None) -> dict:
    return {
        "api": api,
        "called_at_ms": now_ms(),
        "params": data,
        "result": result,
        "success": exception is None,
        "exception_type": type(exception).__name__ if exception else None,
        "exception_message": str(exception) if exception else None,
        "exception_traceback": _traceback(exception) if exception else None,
    }


def _plugin_from_logger(name: str | None) -> str:
    """插件模块都是 plugins.xxx；其它取顶层名（nonebot_plugin_xxx、nonebot、apscheduler……）"""
    parts = (name or "").split(".")
    if parts[0] == "plugins" and len(parts) > 1:
        return parts[1]
    return parts[0]


def log_entry(
    record: dict, full_log: str, run: Run | None, matcher: Matcher | None
) -> dict:
    """run 不为空时日志挂到这次运行上，插件名取 matcher 的"""
    if run is not None:
        plugin_name = type(matcher).plugin_name
    else:
        plugin_name = _plugin_from_logger(record["name"])
    return {
        "created_at_ms": now_ms(),
        "level": record["level"].name,
        "logger_name": record["name"],
        "module_name": record["module"],
        "function_name": record["function"],
        "line": record["line"],
        "message": str(record["message"]),
        "full_log": full_log,
        "run_id": run.run_id if run else None,
        "plugin_name": plugin_name,
    }


def diagnostic_row(entry: dict) -> dict:
    return {
        "created_at_ms": entry["created_at_ms"],
        "level": entry["level"],
        "logger_name": entry["logger_name"],
        "plugin_name": entry["plugin_name"],
        "message_summary": entry["message"][:SUMMARY_LIMIT],
        "full_log_gzip": _gzip(entry["full_log"]),
    }


def _level_no(entry: dict) -> int:
    return logger.level(entry["level"]).no


def _error_fields(
    run: Run, exception: Exception | None
) -> tuple[str | None, str | None]:
    """异常优先，其次是第一次发送失败，最后是第一条 ERROR 日志"""
    if exception is not None:
        return type(exception).__name__, str(exception)
    failed_call = next((call for call in run.api_calls if not call["success"]), None)
    if failed_call is not None:
        return failed_call["exception_type"], failed_call["exception_message"]
    error_log = next((log for log in run.logs if _level_no(log) >= ERROR_NO), None)
    if error_log is not None:
        return "LogError", error_log["message"]
    return None, None


def _event_summary(event: Event) -> str:
    try:
        message = event.get_message()
    except ValueError:
        # 通知、元事件没有消息
        message = ""
    return _truncate(_message_summary(message))


def response_row(
    run: Run, matcher: Matcher, event: Event, exception: Exception | None
) -> dict | None:
    """不需要记录时返回 None"""
    max_log = max(run.logs, key=_level_no, default=None)
    max_level_no = _level_no(max_log) if max_log else 0
    if not run.api_calls and exception is None and max_level_no < ERROR_NO:
        return None

    send_success_count = sum(call["success"] for call in run.api_calls)
    failed = (
        exception is not None
        or send_success_count < len(run.api_calls)
        or max_level_no >= ERROR_NO
    )
    full_diagnostics = failed or max_level_no >= WARNING_NO

    request_raw = response_raw = logs_raw = None
    if full_diagnostics:
        request_raw = json.dumps(
            event.model_dump(), ensure_ascii=False, separators=(",", ":"), default=str
        )
        response_raw = _dumps(run.api_calls)
        logs = list(run.logs)
        if exception is not None:
            logs.append(
                {
                    "level": "ERROR",
                    "kind": "matcher_exception",
                    "exception_type": type(exception).__name__,
                    "exception_message": str(exception),
                    "traceback": _traceback(exception),
                }
            )
        logs_raw = _dumps(logs)

    matcher_class = type(matcher)
    error_type, error_message = _error_fields(run, exception)
    return {
        "run_id": run.run_id,
        "started_at_ms": run.started_at_ms,
        "finished_at_ms": now_ms(),
        "duration_ms": (time.monotonic_ns() - run.started_ns) // 1_000_000,
        "status": "failure" if failed else "success",
        "plugin_name": matcher_class.plugin_name,
        "module_name": matcher_class.module_name,
        "matcher_type": matcher_class.type,
        "matcher_lineno": matcher_class._source.lineno
        if matcher_class._source
        else None,
        "event_name": event.get_event_name(),
        "group_id": _optional_str(getattr(event, "group_id", None)),
        "user_id": _optional_str(getattr(event, "user_id", None)),
        "source_message_id": _optional_str(getattr(event, "message_id", None)),
        "request_summary": _event_summary(event),
        "response_summary": _truncate(
            "\n".join(_message_summary(call["params"]) for call in run.api_calls)
        ),
        "send_count": len(run.api_calls),
        "send_success_count": send_success_count,
        "send_failure_count": len(run.api_calls) - send_success_count,
        "max_log_level": max_log["level"] if max_log else None,
        "error_type": error_type,
        "error_message": error_message,
        "has_full_diagnostics": int(full_diagnostics),
        "request_raw_gzip": _gzip(request_raw),
        "response_raw_gzip": _gzip(response_raw),
        "logs_raw_gzip": _gzip(logs_raw),
    }
