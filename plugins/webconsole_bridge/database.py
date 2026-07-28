from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import aiosqlite

from .config import BridgeConfig
from .models import ProbeResult

SUPPORTED_SCHEMA_VERSION = 1
REQUIRED_TABLES = frozenset(
    {
        "schema_migrations",
        "response_events",
        "diagnostic_logs",
        "bot_status",
    }
)


def _check_path(path: Path, *, kind: str) -> str | None:
    if not path.exists():
        return f"{kind} does not exist: {path}"
    if not path.is_dir():
        return f"{kind} is not a directory: {path}"
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        return f"{kind} is not readable and writable: {path}"
    return None


def _probe_spool_write(spool_path: Path) -> None:
    descriptor, probe_name = tempfile.mkstemp(
        prefix=".webconsole-bridge-probe-",
        dir=spool_path,
    )
    try:
        os.close(descriptor)
    finally:
        try:
            os.unlink(probe_name)
        except FileNotFoundError:
            pass


async def probe_storage(config: BridgeConfig) -> ProbeResult:
    data_error = _check_path(config.database_path.parent, kind="data directory")
    if data_error:
        return ProbeResult.failure(data_error)

    if not config.database_path.exists():
        return ProbeResult.failure(
            f"database does not exist: {config.database_path}"
        )
    if not config.database_path.is_file():
        return ProbeResult.failure(
            f"database path is not a file: {config.database_path}"
        )
    if not os.access(config.database_path, os.R_OK | os.W_OK):
        return ProbeResult.failure(
            f"database is not readable and writable: {config.database_path}"
        )

    spool_error = _check_path(config.spool_path, kind="spool directory")
    if spool_error:
        return ProbeResult.failure(spool_error)

    try:
        await asyncio.to_thread(_probe_spool_write, config.spool_path)
    except OSError as exc:
        return ProbeResult.failure(
            f"spool write probe failed: {type(exc).__name__}: {exc}"
        )

    database_uri = f"file:{quote(str(config.database_path), safe='/')}?mode=rw"
    try:
        async with aiosqlite.connect(database_uri, uri=True) as connection:
            await connection.execute("PRAGMA busy_timeout = 5000")
            cursor = await connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            )
            row = await cursor.fetchone()
            await cursor.close()
            version = int(row[0]) if row else 0
            if version != SUPPORTED_SCHEMA_VERSION:
                return ProbeResult.failure(
                    "unsupported database schema version "
                    f"{version}; expected {SUPPORTED_SCHEMA_VERSION}"
                )

            placeholders = ",".join("?" for _ in REQUIRED_TABLES)
            cursor = await connection.execute(
                "SELECT name FROM sqlite_schema "
                f"WHERE type = 'table' AND name IN ({placeholders})",
                tuple(sorted(REQUIRED_TABLES)),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            present = {str(row[0]) for row in rows}
            missing = sorted(REQUIRED_TABLES - present)
            if missing:
                return ProbeResult.failure(
                    f"database schema is missing tables: {', '.join(missing)}"
                )
    except (aiosqlite.Error, OSError) as exc:
        return ProbeResult.failure(
            f"database probe failed: {type(exc).__name__}: {exc}"
        )

    return ProbeResult.success()

