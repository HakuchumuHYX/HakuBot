from __future__ import annotations

import base64
import json
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher, current_matcher
from nonebot.message import run_postprocessor, run_preprocessor

from .status import BridgeRuntime

RUN_CONTEXT_STATE_KEY = "_webconsole_bridge_run_context"
RESPONSE_API_NAMES = frozenset(
    {
        "send_msg",
        "send_group_msg",
        "send_private_msg",
        "send_group_forward_msg",
        "send_private_forward_msg",
    }
)
SUMMARY_LIMIT = 2_000
LOG_LEVEL_WEIGHT = {
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


def _string_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _event_name(event: Event) -> str:
    try:
        return str(event.get_event_name())
    except Exception:
        return type(event).__name__


def _serialize_event(event: Event) -> str:
    try:
        payload = event.model_dump(mode="json")
    except Exception:
        try:
            payload = event.dict()
        except Exception:
            payload = vars(event)
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _truncate_summary(value: str) -> str:
    if len(value) <= SUMMARY_LIMIT:
        return value
    return value[:SUMMARY_LIMIT] + "…"


def _message_summary(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return "[二进制]"
    if isinstance(value, (list, tuple)):
        return "".join(_message_summary(item) for item in value)
    if hasattr(value, "type") and hasattr(value, "data"):
        segment_type = str(value.type)
        data = value.data
        if segment_type == "text" and isinstance(data, dict):
            return str(data.get("text", ""))
        return f"[{segment_type}]"
    if isinstance(value, dict):
        if "type" in value and "data" in value:
            segment_type = str(value["type"])
            data = value["data"]
            if segment_type == "text" and isinstance(data, dict):
                return str(data.get("text", ""))
            return f"[{segment_type}]"
        if "message" in value:
            return _message_summary(value["message"])
        return serialize_api_value(value)
    return str(value)


def _event_summary(event: Event) -> str:
    try:
        message = event.get_message()
    except Exception:
        message = getattr(event, "message", "")
    return _truncate_summary(_message_summary(message))


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "__type__": "bytes",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, Path):
        return str(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if hasattr(value, "type") and hasattr(value, "data"):
        return {
            "type": str(value.type),
            "data": value.data,
        }
    return repr(value)


def serialize_api_value(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _matcher_source(matcher: Matcher) -> tuple[
    str | None,
    str | None,
    str | None,
    int | None,
]:
    matcher_class = type(matcher)
    source = getattr(matcher_class, "_source", None)
    plugin_id = getattr(source, "plugin_id", None)
    module_name = getattr(source, "module_name", None) or getattr(
        matcher_class, "module_name", None
    )
    lineno = getattr(source, "lineno", None)
    try:
        plugin_name = getattr(source, "plugin_name", None)
    except Exception:
        plugin_name = None
    return plugin_name, plugin_id, module_name, lineno


@dataclass(slots=True)
class APICallRecord:
    api: str
    called_at_ms: int
    params_raw: str
    result_raw: str
    success: bool
    exception_type: str | None = None
    exception_message: str | None = None
    exception_traceback: str | None = None


@dataclass(slots=True)
class DiagnosticLogEntry:
    created_at_ms: int
    level: str
    logger_name: str | None
    module_name: str | None
    function_name: str | None
    line: int | None
    message: str
    full_log: str
    run_id: str | None = None
    plugin_name: str | None = None


@dataclass(slots=True)
class RunContext:
    run_id: str
    started_at_ms: int
    started_monotonic_ns: int
    plugin_name: str | None
    plugin_id: str | None
    module_name: str | None
    matcher_type: str
    matcher_lineno: int | None
    bot_id: str | None
    event_name: str
    group_id: str | None
    user_id: str | None
    source_message_id: str | None
    request_summary: str
    request_raw: str
    finished_at_ms: int | None = None
    duration_ms: int | None = None
    matcher_exception: Exception | None = None
    matcher_traceback: str | None = None
    api_calls: list[Any] = field(default_factory=list)
    diagnostic_logs: list[Any] = field(default_factory=list)

    @property
    def should_record(self) -> bool:
        if self.api_calls or self.matcher_exception is not None:
            return True
        return any(
            LOG_LEVEL_WEIGHT.get(str(getattr(item, "level", "")), 0)
            >= LOG_LEVEL_WEIGHT["ERROR"]
            for item in self.diagnostic_logs
        )


@dataclass(slots=True)
class CompletedResponse:
    context: RunContext
    status: str
    send_count: int
    send_success_count: int
    send_failure_count: int
    response_summary: str
    max_log_level: str | None
    error_type: str | None
    error_message: str | None
    has_full_diagnostics: bool
    request_raw: str | None
    response_raw: str | None
    logs_raw: str | None


FinalizeCallback = Callable[[RunContext, CompletedResponse | None], Awaitable[None]]


class CaptureManager:
    def __init__(self) -> None:
        self._finalize_callback: FinalizeCallback | None = None

    def set_finalize_callback(
        self,
        callback: FinalizeCallback | None,
    ) -> None:
        self._finalize_callback = callback

    def begin_run(
        self,
        matcher: Matcher,
        bot: Bot,
        event: Event,
    ) -> RunContext:
        plugin_name, plugin_id, module_name, lineno = _matcher_source(matcher)
        now_ms = time.time_ns() // 1_000_000
        context = RunContext(
            run_id=uuid.uuid4().hex,
            started_at_ms=now_ms,
            started_monotonic_ns=time.monotonic_ns(),
            plugin_name=plugin_name,
            plugin_id=plugin_id,
            module_name=module_name,
            matcher_type=str(getattr(type(matcher), "type", "")),
            matcher_lineno=lineno,
            bot_id=_string_or_none(getattr(bot, "self_id", None)),
            event_name=_event_name(event),
            group_id=_string_or_none(getattr(event, "group_id", None)),
            user_id=_string_or_none(getattr(event, "user_id", None)),
            source_message_id=_string_or_none(
                getattr(event, "message_id", None)
            ),
            request_summary=_event_summary(event),
            request_raw=_serialize_event(event),
        )
        matcher.state[RUN_CONTEXT_STATE_KEY] = context
        return context

    def current_run(self, matcher: Matcher) -> RunContext | None:
        context = matcher.state.get(RUN_CONTEXT_STATE_KEY)
        return context if isinstance(context, RunContext) else None

    def record_api_call(
        self,
        matcher: Matcher,
        *,
        api: str,
        data: dict[str, Any],
        result: Any,
        exception: Exception | None,
    ) -> APICallRecord | None:
        if api not in RESPONSE_API_NAMES:
            return None
        context = self.current_run(matcher)
        if context is None:
            return None

        exception_traceback = None
        if exception is not None:
            exception_traceback = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
        record = APICallRecord(
            api=api,
            called_at_ms=time.time_ns() // 1_000_000,
            params_raw=serialize_api_value(data),
            result_raw=serialize_api_value(result),
            success=exception is None,
            exception_type=(
                type(exception).__name__ if exception is not None else None
            ),
            exception_message=str(exception) if exception is not None else None,
            exception_traceback=exception_traceback,
        )
        context.api_calls.append(record)
        return record

    def record_diagnostic(
        self,
        entry: DiagnosticLogEntry,
        matcher: Matcher | None,
    ) -> RunContext | None:
        if matcher is None:
            return None
        context = self.current_run(matcher)
        if context is None:
            return None
        entry.run_id = context.run_id
        entry.plugin_name = context.plugin_name
        context.diagnostic_logs.append(entry)
        return context

    def finish_run(
        self,
        matcher: Matcher,
        exception: Exception | None,
    ) -> RunContext | None:
        context = matcher.state.pop(RUN_CONTEXT_STATE_KEY, None)
        if not isinstance(context, RunContext):
            return None
        context.finished_at_ms = time.time_ns() // 1_000_000
        elapsed_ns = max(0, time.monotonic_ns() - context.started_monotonic_ns)
        context.duration_ms = elapsed_ns // 1_000_000
        context.matcher_exception = exception
        if exception is not None:
            context.matcher_traceback = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
        return context

    def complete_response(
        self,
        context: RunContext,
    ) -> CompletedResponse | None:
        if not context.should_record:
            return None

        send_count = len(context.api_calls)
        send_success_count = sum(
            1 for call in context.api_calls if call.success
        )
        send_failure_count = send_count - send_success_count

        max_log_level = None
        max_log_weight = 0
        for item in context.diagnostic_logs:
            level = str(getattr(item, "level", ""))
            weight = LOG_LEVEL_WEIGHT.get(level, 0)
            if weight > max_log_weight:
                max_log_level = level
                max_log_weight = weight

        failed = (
            context.matcher_exception is not None
            or send_failure_count > 0
            or max_log_weight >= LOG_LEVEL_WEIGHT["ERROR"]
        )
        status = "failure" if failed else "success"
        has_full_diagnostics = failed or max_log_weight >= LOG_LEVEL_WEIGHT[
            "WARNING"
        ]

        error_type = None
        error_message = None
        if context.matcher_exception is not None:
            error_type = type(context.matcher_exception).__name__
            error_message = str(context.matcher_exception)
        else:
            failed_call = next(
                (call for call in context.api_calls if not call.success),
                None,
            )
            if failed_call is not None:
                error_type = failed_call.exception_type
                error_message = failed_call.exception_message
            elif max_log_weight >= LOG_LEVEL_WEIGHT["ERROR"]:
                error_log = next(
                    (
                        item
                        for item in context.diagnostic_logs
                        if LOG_LEVEL_WEIGHT.get(
                            str(getattr(item, "level", "")),
                            0,
                        )
                        >= LOG_LEVEL_WEIGHT["ERROR"]
                    ),
                    None,
                )
                if error_log is not None:
                    error_type = "LogError"
                    error_message = str(getattr(error_log, "message", ""))

        response_summary = _truncate_summary(
            "\n".join(
                _message_summary(json.loads(call.params_raw))
                for call in context.api_calls
            )
        )

        request_raw = None
        response_raw = None
        logs_raw = None
        if has_full_diagnostics:
            request_raw = context.request_raw
            response_raw = json.dumps(
                [
                    {
                        **asdict(call),
                        "params": json.loads(call.params_raw),
                        "result": json.loads(call.result_raw),
                    }
                    for call in context.api_calls
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            log_payload = [
                asdict(item) if hasattr(item, "__dataclass_fields__") else item
                for item in context.diagnostic_logs
            ]
            if context.matcher_exception is not None:
                log_payload.append(
                    {
                        "level": "ERROR",
                        "kind": "matcher_exception",
                        "exception_type": type(
                            context.matcher_exception
                        ).__name__,
                        "exception_message": str(context.matcher_exception),
                        "traceback": context.matcher_traceback,
                    }
                )
            logs_raw = json.dumps(
                log_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=_json_default,
            )

        return CompletedResponse(
            context=context,
            status=status,
            send_count=send_count,
            send_success_count=send_success_count,
            send_failure_count=send_failure_count,
            response_summary=response_summary,
            max_log_level=max_log_level,
            error_type=error_type,
            error_message=error_message,
            has_full_diagnostics=has_full_diagnostics,
            request_raw=request_raw,
            response_raw=response_raw,
            logs_raw=logs_raw,
        )

    async def finalize_run(
        self,
        matcher: Matcher,
        exception: Exception | None,
    ) -> CompletedResponse | None:
        context = self.finish_run(matcher, exception)
        if context is None:
            return None
        response = self.complete_response(context)
        if self._finalize_callback is not None and (
            response is not None or context.diagnostic_logs
        ):
            await self._finalize_callback(context, response)
        return response


capture_manager = CaptureManager()
_hooks_registered = False


def register_capture_hooks(runtime: BridgeRuntime) -> None:
    global _hooks_registered
    if _hooks_registered:
        return
    _hooks_registered = True

    @run_preprocessor
    async def _webconsole_run_preprocessor(
        matcher: Matcher,
        bot: Bot,
        event: Event,
    ) -> None:
        if not runtime.available:
            return
        capture_manager.begin_run(matcher, bot, event)

    @run_postprocessor
    async def _webconsole_run_postprocessor(
        matcher: Matcher,
        exception: Exception | None,
    ) -> None:
        await capture_manager.finalize_run(matcher, exception)

    @Bot.on_called_api
    async def _webconsole_called_api(
        bot: Bot,
        exception: Exception | None,
        api: str,
        data: dict[str, Any],
        result: Any,
    ) -> None:
        if not runtime.available or api not in RESPONSE_API_NAMES:
            return
        matcher = current_matcher.get(None)
        if matcher is None:
            return
        capture_manager.record_api_call(
            matcher,
            api=api,
            data=data,
            result=result,
            exception=exception,
        )
