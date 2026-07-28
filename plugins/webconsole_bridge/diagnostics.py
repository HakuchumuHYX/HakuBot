from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from loguru import logger
from nonebot.matcher import current_matcher

from .capture import DiagnosticLogEntry, capture_manager
from .persistence import PersistenceWriter
from .status import BridgeRuntime


def _plugin_name_from_record(record: Any) -> str | None:
    """Resolve the owning plugin without relying on a matcher's context."""
    file_record = record.get("file")
    file_path = getattr(file_record, "path", None)
    if file_path:
        parts = Path(str(file_path)).parts
        for index in range(len(parts) - 1, -1, -1):
            if parts[index] == "plugins" and index + 1 < len(parts):
                return parts[index + 1]

    logger_name = record.get("name")
    if logger_name:
        components = str(logger_name).split(".")
        if len(components) >= 2 and components[0] == "plugins":
            return components[1]
        if components[0].startswith("nonebot_plugin_"):
            return components[0]
    return None


class DiagnosticCapture:
    def __init__(
        self,
        runtime: BridgeRuntime,
        persistence: PersistenceWriter,
    ) -> None:
        self.runtime = runtime
        self.persistence = persistence
        self._sink_id: int | None = None

    def start(self) -> None:
        if self._sink_id is not None:
            return
        self._sink_id = logger.add(
            self._sink,
            level="WARNING",
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
                "{name}:{function}:{line} - {message}"
            ),
            diagnose=False,
            backtrace=True,
            enqueue=False,
        )

    def stop(self) -> None:
        sink_id = self._sink_id
        self._sink_id = None
        if sink_id is not None:
            logger.remove(sink_id)

    def _sink(self, message: Any) -> None:
        record = message.record
        if record["extra"].get("webconsole_bridge_internal"):
            return
        if not self.runtime.available:
            return

        level = str(record["level"].name)
        if level not in {"WARNING", "ERROR", "CRITICAL"}:
            return
        entry = DiagnosticLogEntry(
            created_at_ms=time.time_ns() // 1_000_000,
            level=level,
            logger_name=(
                str(record["name"]) if record["name"] is not None else None
            ),
            module_name=(
                str(record["module"])
                if record["module"] is not None
                else None
            ),
            function_name=(
                str(record["function"])
                if record["function"] is not None
                else None
            ),
            line=int(record["line"]) if record["line"] is not None else None,
            message=str(record["message"]),
            full_log=str(message),
            plugin_name=_plugin_name_from_record(record),
        )
        matcher = current_matcher.get(None)
        capture_manager.record_diagnostic(entry, matcher)
        self.persistence.persist_log_from_sink(entry)
