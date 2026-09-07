"""Bounded downloads with explicit proxy modes and owned, isolated sessions."""

import asyncio
import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import tempfile
import aiohttp
from PIL import Image
from utils.json_io import atomic_write_bytes

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=30, sock_read=120)
INSECURE_SSL = os.environ.get("HAKUBOT_INSECURE_SSL", "").strip() in (
    "1",
    "true",
    "yes",
)
_sessions = {}


class HttpError(RuntimeError):
    def __init__(self, status_code=500, message=""):
        self.status_code = status_code
        self.message = message
        super().__init__(f"{status_code}: {message}")


class DownloadTooLarge(ValueError):
    pass


def normalize_proxy(proxy):
    if not proxy:
        return None
    proxy = str(proxy).strip()
    while proxy.startswith(("http://http://", "https://https://")):
        proxy = proxy.split("://", 1)[1]
    return proxy if "://" in proxy else f"http://{proxy}"


def get_effective_proxy(explicit_proxy=None):
    """None inherits environment, False requests direct access, str selects a proxy."""
    if explicit_proxy is False:
        return None
    if explicit_proxy:
        return normalize_proxy(explicit_proxy)
    return next(
        (
            normalize_proxy(os.environ[k])
            for k in (
                "HTTPS_PROXY",
                "https_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "ALL_PROXY",
                "all_proxy",
            )
            if os.environ.get(k)
        ),
        None,
    )


def get_client_session(namespace="public"):
    session = _sessions.get(namespace)
    if session is None or session.closed:
        jar = aiohttp.CookieJar() if namespace != "public" else aiohttp.DummyCookieJar()
        session = aiohttp.ClientSession(
            trust_env=True, timeout=DEFAULT_TIMEOUT, cookie_jar=jar
        )
        _sessions[namespace] = session
    return session


async def close_sessions():
    sessions = tuple(_sessions.values())
    _sessions.clear()
    await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)


async def read_limited_response(response, max_bytes):
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise DownloadTooLarge(f"Download exceeds {max_bytes} bytes")
    chunks = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise DownloadTooLarge(f"Download exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def download_bytes(
    url,
    *,
    proxy=None,
    headers=None,
    max_bytes=20 * 1024 * 1024,
    timeout=120,
    attempts=3,
):
    if max_bytes <= 0 or attempts < 1:
        raise ValueError("max_bytes and attempts must be positive")
    # A direct session must not consult environment proxy settings.
    direct = None
    if proxy is False:
        direct = aiohttp.ClientSession(
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar()
        )
    try:
        session = direct or get_client_session()
        for attempt in range(attempts):
            try:
                async with session.get(
                    url,
                    headers=headers,
                    proxy=get_effective_proxy(proxy),
                    ssl=False if INSECURE_SSL else None,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status != 200:
                        raise HttpError(response.status, "Download failed")
                    return await read_limited_response(response, max_bytes)
            except (aiohttp.ClientError, asyncio.TimeoutError, HttpError) as exc:
                if isinstance(exc, HttpError) and exc.status_code not in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    raise
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)
    finally:
        if direct is not None:
            await direct.close()


async def download_to_file(url, path, **options):
    """Stream to a staging file; failure or cancellation never truncates the target."""
    import aiofiles

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = options.pop("max_bytes", 20 * 1024 * 1024)
    timeout = options.pop("timeout", 120)
    attempts = options.pop("attempts", 3)
    proxy = options.pop("proxy", None)
    statuses = options.pop("allowed_statuses", (200,))
    if max_bytes <= 0 or attempts < 1:
        raise ValueError("Invalid download bounds")
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(descriptor)
    direct = (
        aiohttp.ClientSession(trust_env=False, cookie_jar=aiohttp.DummyCookieJar())
        if proxy is False
        else None
    )
    try:
        session = direct or get_client_session()
        for attempt in range(attempts):
            try:
                async with session.get(
                    url,
                    proxy=get_effective_proxy(proxy),
                    ssl=False if INSECURE_SSL else None,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    **options,
                ) as response:
                    if response.status not in statuses:
                        raise HttpError(response.status, "Download failed")
                    declared = response.headers.get("Content-Length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise DownloadTooLarge(f"Download exceeds {max_bytes} bytes")
                    size = 0
                    async with aiofiles.open(name, "wb") as stream:
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            size += len(chunk)
                            if size > max_bytes:
                                raise DownloadTooLarge(
                                    f"Download exceeds {max_bytes} bytes"
                                )
                            await stream.write(chunk)
                        await stream.flush()
                os.replace(name, path)
                return path
            except (aiohttp.ClientError, asyncio.TimeoutError, HttpError) as exc:
                if isinstance(exc, HttpError) and exc.status_code not in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    raise
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)
    finally:
        Path(name).unlink(missing_ok=True)
        if direct is not None:
            await direct.close()


@asynccontextmanager
async def temporary_download(url, *, ext="tmp", **options):
    descriptor, name = tempfile.mkstemp(suffix="." + ext.lstrip("."))
    os.close(descriptor)
    path = Path(name)
    try:
        await download_to_file(url, path, **options)
        yield path
    finally:
        path.unlink(missing_ok=True)


async def download_image(url, force_http=False, **options):
    if force_http and url.startswith("https://"):
        url = "http://" + url[8:]
    data = await download_bytes(url, **options)
    with Image.open(BytesIO(data)) as image:
        image.load()
        return image.copy()
