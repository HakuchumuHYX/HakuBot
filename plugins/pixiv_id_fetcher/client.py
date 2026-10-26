"""Pixiv App API：登录、作品详情、下载图片、动图合成 GIF"""

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlsplit
from zipfile import ZipFile

import aiohttp
from PIL import Image

from plugins.pixiv_id_fetcher.config import config
from utils.images.formats import detect_format, image_to_bytes, to_rgb
from utils.network.http import (
    HttpError,
    download_bytes,
    get_client_session,
    get_effective_proxy,
)

OAUTH_URL = "https://oauth.secure.pixiv.net/auth/token"
API_BASE = "https://app-api.pixiv.net"
HASH_SECRET = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"
# i.pximg.net 不带 Referer 会 403；反代域名不需要，带上也无妨
IMAGE_HEADERS = {"Referer": "https://www.pixiv.net/", "User-Agent": "Mozilla/5.0"}

_access_token = ""
_expires_at = 0.0


class PixivError(Exception):
    """文案直接回复给用户"""


@dataclass(frozen=True)
class Illust:
    pid: int
    title: str
    author: str
    x_restrict: int  # 0 全年龄，1 R18，2 R18G
    is_ugoira: bool
    urls: list[str]  # 每页一个，按 send_original 选原图或 large


def _app_headers() -> dict[str, str]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "User-Agent": "PixivAndroidApp/5.0.234 (Android 11; HakuBot)",
        "App-OS": "android",
        "App-OS-Version": "11",
        "App-Version": "5.0.234",
        "Accept-Language": "zh-CN",
        "X-Client-Time": now,
        "X-Client-Hash": hashlib.md5(f"{now}{HASH_SECRET}".encode()).hexdigest(),
    }


async def _request(method: str, url: str, *, headers=None, **kwargs) -> dict:
    async with get_client_session().request(
        method,
        url,
        headers={**_app_headers(), **(headers or {})},
        proxy=get_effective_proxy(config.proxy),
        timeout=aiohttp.ClientTimeout(total=config.timeout),
        **kwargs,
    ) as response:
        if response.status != 200:
            raise HttpError(response.status, "Pixiv 请求失败")
        return await response.json()


async def _token() -> str:
    global _access_token, _expires_at
    if time.time() < _expires_at - 60:
        return _access_token
    try:
        payload = await _request(
            "POST",
            OAUTH_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": config.refresh_token,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "get_secure_url": "true",
                "include_policy": "true",
            },
        )
    except HttpError as e:
        raise PixivError("Pixiv 登录失败，请检查 refresh_token 配置") from e
    _access_token = payload["access_token"]
    _expires_at = time.time() + payload["expires_in"]
    return _access_token


async def _api_get(path: str, pid: int) -> dict:
    token = await _token()
    return await _request(
        "GET",
        f"{API_BASE}{path}",
        params={"illust_id": pid},
        headers={"Authorization": f"Bearer {token}"},
    )


def _rewrite_domain(url: str) -> str:
    if not config.reverse_proxy_domain:
        return url
    return urlsplit(url)._replace(netloc=config.reverse_proxy_domain).geturl()


async def fetch_illust(pid: int) -> Illust:
    item = (await _api_get("/v1/illust/detail", pid))["illust"]
    if item["meta_pages"]:
        size = "original" if config.send_original else "large"
        urls = [page["image_urls"][size] for page in item["meta_pages"]]
    elif config.send_original:
        urls = [item["meta_single_page"]["original_image_url"]]
    else:
        urls = [item["image_urls"]["large"]]
    return Illust(
        pid=item["id"],
        title=item["title"],
        author=item["user"]["name"],
        x_restrict=item["x_restrict"],
        is_ugoira=item["type"] == "ugoira",
        urls=[_rewrite_domain(url) for url in urls],
    )


async def download_image(url: str) -> bytes:
    data = await download_bytes(
        url, proxy=config.proxy, headers=IMAGE_HEADERS, max_bytes=config.max_bytes
    )
    # large 地址（c/600x1200_90_webp/…）实际返回 webp，统一转成 jpg 再发
    if detect_format(data) == "webp":
        with Image.open(BytesIO(data)) as image:
            data = image_to_bytes(to_rgb(image), "JPEG", quality=92)
    return data


async def fetch_ugoira_gif(pid: int) -> bytes:
    metadata = (await _api_get("/v1/ugoira/metadata", pid))["ugoira_metadata"]
    frames = metadata["frames"]
    if len(frames) > config.ugoira_max_frames:
        raise PixivError(
            f"动图有 {len(frames)} 帧，超过 {config.ugoira_max_frames} 帧上限"
        )
    zip_data = await download_bytes(
        metadata["zip_urls"]["medium"],
        proxy=config.proxy,
        headers=IMAGE_HEADERS,
        max_bytes=config.ugoira_zip_max_bytes,
    )
    gif = await asyncio.to_thread(_render_gif, zip_data, frames)
    if len(gif) > config.max_bytes:
        raise PixivError("动图合成的 GIF 过大，已停止发送")
    return gif


def _render_gif(zip_data: bytes, frames: list[dict]) -> bytes:
    images = []
    with ZipFile(BytesIO(zip_data)) as archive:
        for frame in frames:
            with archive.open(frame["file"]) as f:
                images.append(Image.open(f).convert("RGB"))
    first, *rest = images
    output = BytesIO()
    first.save(
        output,
        format="GIF",
        save_all=True,
        append_images=rest,
        duration=[frame["delay"] for frame in frames],
        loop=0,
        disposal=2,
    )
    return output.getvalue()
