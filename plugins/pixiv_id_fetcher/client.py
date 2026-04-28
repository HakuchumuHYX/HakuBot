from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zipfile import ZipFile

import httpx
from PIL import Image

from .models import PixivIllust, PixivPage, UgoiraFrame, UgoiraMetadata


OAUTH_URL = "https://oauth.secure.pixiv.net/auth/token"
API_BASE = "https://app-api.pixiv.net"
REFERER = "https://www.pixiv.net/"
HASH_SECRET = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"


class PixivClientError(Exception):
    kind = "generic"


class PixivAuthError(PixivClientError):
    kind = "auth"


class PixivNotFoundError(PixivClientError):
    kind = "not_found"


class PixivForbiddenError(PixivClientError):
    kind = "forbidden"


class PixivRateLimitedError(PixivClientError):
    kind = "rate_limited"


class PixivTimeoutError(PixivClientError):
    kind = "timeout"


class PixivNetworkError(PixivClientError):
    kind = "network"


class PixivTooLargeError(PixivClientError):
    kind = "too_large"


class PixivUgoiraError(PixivClientError):
    kind = "ugoira"


class PixivSendForwardError(PixivClientError):
    kind = "send_forward"


class PixivClient:
    def __init__(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        proxy: Optional[str] = None,
        timeout: float = 20.0,
        reverse_proxy_domain: Optional[str] = None,
    ) -> None:
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._proxy = proxy
        self._timeout = timeout
        self._reverse_proxy_domain = reverse_proxy_domain
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._auth_lock = asyncio.Lock()

    def _client_kwargs(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        client_hash = hashlib.md5(f"{now}{HASH_SECRET}".encode("utf-8")).hexdigest()
        kwargs: Dict[str, Any] = {
            "timeout": self._timeout,
            "follow_redirects": True,
            "headers": {
                "User-Agent": "PixivAndroidApp/5.0.234 (Android 11; HakuBot)",
                "App-OS": "android",
                "App-OS-Version": "11",
                "App-Version": "5.0.234",
                "Accept-Language": "zh-CN",
                "X-Client-Time": now,
                "X-Client-Hash": client_hash,
            },
        }
        if self._proxy:
            kwargs["proxy"] = self._proxy
        return kwargs

    async def _request_with_proxy_compat(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        client_kwargs = self._client_kwargs()
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                return await client.request(method, url, **kwargs)
        except TypeError:
            if self._proxy:
                client_kwargs.pop("proxy", None)
                client_kwargs["proxies"] = self._proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                return await client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise PixivTimeoutError(str(e)) from e
        except httpx.RequestError as e:
            raise PixivNetworkError(str(e)) from e

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, not_found_message: str) -> None:
        if resp.status_code == 404:
            raise PixivNotFoundError(not_found_message)
        if resp.status_code == 403:
            raise PixivForbiddenError(f"Pixiv 拒绝请求: HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise PixivRateLimitedError(f"Pixiv 请求过于频繁: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise PixivClientError(f"Pixiv 请求失败: HTTP {resp.status_code}")

    async def _ensure_access_token(self) -> str:
        if self._access_token and time.time() < self._access_token_expires_at - 60:
            return self._access_token

        async with self._auth_lock:
            if self._access_token and time.time() < self._access_token_expires_at - 60:
                return self._access_token

            if not self._refresh_token:
                raise PixivAuthError("未配置 pixiv refresh_token")

            data = {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "get_secure_url": "true",
                "include_policy": "true",
            }
            resp = await self._request_with_proxy_compat("POST", OAUTH_URL, data=data)
            if resp.status_code >= 400:
                raise PixivAuthError(f"Pixiv 登录失败: HTTP {resp.status_code}")

            payload = resp.json()
            if isinstance(payload.get("response"), dict):
                payload = payload["response"]
            token = str(payload.get("access_token") or "")
            if not token:
                raise PixivAuthError("Pixiv 登录失败: 响应中没有 access_token")

            expires_in = int(payload.get("expires_in") or 3600)
            self._access_token = token
            self._access_token_expires_at = time.time() + expires_in
            return token

    async def fetch_illust(self, pid: int, *, send_original: bool = False) -> PixivIllust:
        token = await self._ensure_access_token()
        resp = await self._request_with_proxy_compat(
            "GET",
            f"{API_BASE}/v1/illust/detail",
            params={"illust_id": pid},
            headers={"Authorization": f"Bearer {token}"},
        )

        self._raise_for_status(resp, not_found_message="作品不存在、已删除或当前账号无权限查看")

        payload = resp.json()
        item = payload.get("illust")
        if not isinstance(item, dict):
            raise PixivNotFoundError("Pixiv API 响应中没有作品信息")

        return self._parse_illust(item, send_original=send_original)

    async def fetch_ugoira_metadata(self, pid: int) -> UgoiraMetadata:
        token = await self._ensure_access_token()
        resp = await self._request_with_proxy_compat(
            "GET",
            f"{API_BASE}/v1/ugoira/metadata",
            params={"illust_id": pid},
            headers={"Authorization": f"Bearer {token}"},
        )
        self._raise_for_status(resp, not_found_message="动图元数据不存在或当前账号无权限查看")
        return self.parse_ugoira_metadata(resp.json())

    async def download_image(self, url: str, *, max_bytes: int) -> bytes:
        return await self.download_bytes(url, max_bytes=max_bytes)

    async def download_bytes(self, url: str, *, max_bytes: int) -> bytes:
        resp = await self._request_with_proxy_compat(
            "GET",
            url,
            headers={
                "Referer": REFERER,
                "User-Agent": "Mozilla/5.0",
            },
        )
        self._raise_for_status(resp, not_found_message="图片文件不存在或已失效")

        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise PixivTooLargeError(f"文件过大: {content_length} bytes")

        data = resp.content
        if len(data) > max_bytes:
            raise PixivTooLargeError(f"文件过大: {len(data)} bytes")
        return data

    @staticmethod
    def parse_ugoira_metadata(payload: Dict[str, Any]) -> UgoiraMetadata:
        metadata = payload.get("ugoira_metadata")
        if not isinstance(metadata, dict):
            raise PixivUgoiraError("响应中没有 ugoira_metadata")

        zip_urls = metadata.get("zip_urls")
        zip_url = ""
        if isinstance(zip_urls, dict):
            zip_url = str(zip_urls.get("medium") or "")
        if not zip_url:
            raise PixivUgoiraError("响应中没有动图 zip 地址")

        frames_raw = metadata.get("frames")
        if not isinstance(frames_raw, list) or not frames_raw:
            raise PixivUgoiraError("响应中没有动图帧信息")

        frames: List[UgoiraFrame] = []
        for item in frames_raw:
            if not isinstance(item, dict):
                continue
            file = str(item.get("file") or "")
            delay = int(item.get("delay") or 0)
            if file and delay > 0:
                frames.append(UgoiraFrame(file=file, delay=delay))

        if not frames:
            raise PixivUgoiraError("动图帧信息无效")

        return UgoiraMetadata(zip_url=zip_url, frames=frames)

    @staticmethod
    def render_ugoira_gif(
        zip_data: bytes,
        metadata: UgoiraMetadata,
        *,
        max_frames: int,
        max_bytes: int,
    ) -> bytes:
        if len(metadata.frames) > max_frames:
            raise PixivTooLargeError(f"动图帧数过多: {len(metadata.frames)}")

        try:
            with ZipFile(BytesIO(zip_data)) as archive:
                images: List[Image.Image] = []
                durations: List[int] = []
                for frame in metadata.frames:
                    with archive.open(frame.file) as f:
                        image = Image.open(f).convert("RGB")
                        images.append(image.copy())
                    durations.append(frame.delay)
        except Exception as e:
            raise PixivUgoiraError(f"解压动图失败: {e}") from e

        if not images:
            raise PixivUgoiraError("动图没有可用帧")

        output = BytesIO()
        first, rest = images[0], images[1:]
        try:
            first.save(
                output,
                format="GIF",
                save_all=True,
                append_images=rest,
                duration=durations,
                loop=0,
                disposal=2,
            )
        except Exception as e:
            raise PixivUgoiraError(f"合成 GIF 失败: {e}") from e
        data = output.getvalue()
        if len(data) > max_bytes:
            raise PixivTooLargeError(f"动图 GIF 过大: {len(data)} bytes")
        return data

    @staticmethod
    def normalize_static_image_for_forward(data: bytes, ext: str) -> tuple[bytes, str]:
        ext = ext.lower().lstrip(".")
        if ext not in {"webp"}:
            return data, ext

        try:
            with Image.open(BytesIO(data)) as image:
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=92)
                return output.getvalue(), "jpg"
        except Exception as e:
            raise PixivClientError(f"转换合并转发图片失败: {e}") from e

    def _parse_illust(self, item: Dict[str, Any], *, send_original: bool) -> PixivIllust:
        pid = int(item.get("id") or 0)
        title = str(item.get("title") or "")
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        author = str(user.get("name") or "")
        author_id = int(user.get("id") or 0)
        x_restrict = int(item.get("x_restrict") or 0)
        page_count = int(item.get("page_count") or 1)
        illust_type = str(item.get("type") or "illust")

        meta_pages = item.get("meta_pages")
        pages: List[PixivPage] = []
        if isinstance(meta_pages, list) and meta_pages:
            for index, page in enumerate(meta_pages):
                if not isinstance(page, dict):
                    continue
                image_urls = page.get("image_urls")
                if isinstance(image_urls, dict):
                    pages.append(self._build_page(index, image_urls, send_original=send_original))
        else:
            image_urls = item.get("image_urls")
            meta_single = item.get("meta_single_page")
            if isinstance(meta_single, dict) and meta_single.get("original_image_url"):
                image_urls = {**(image_urls if isinstance(image_urls, dict) else {})}
                image_urls["original"] = meta_single.get("original_image_url")
            if isinstance(image_urls, dict):
                pages.append(self._build_page(0, image_urls, send_original=send_original))

        return PixivIllust(
            pid=pid,
            title=title,
            author=author,
            author_id=author_id,
            x_restrict=x_restrict,
            page_count=max(page_count, len(pages) or 1),
            pages=pages,
            web_url=f"https://www.pixiv.net/artworks/{pid}",
            illust_type=illust_type,
        )

    def _build_page(self, index: int, image_urls: Dict[str, Any], *, send_original: bool) -> PixivPage:
        original_url = str(image_urls.get("original") or "")
        regular_url = str(
            image_urls.get("large")
            or image_urls.get("medium")
            or image_urls.get("square_medium")
            or original_url
        )
        url = original_url if send_original and original_url else regular_url
        url = self._rewrite_domain(url)
        original_url = self._rewrite_domain(original_url) if original_url else ""
        ext = self._guess_ext(original_url or url)
        return PixivPage(index=index, url=url, original_url=original_url, ext=ext)

    def _rewrite_domain(self, url: str) -> str:
        if not self._reverse_proxy_domain or not url:
            return url
        match = re.search(r"://(.*?)/", url)
        if not match:
            return url
        return url.replace(match.group(1), self._reverse_proxy_domain, 1)

    @staticmethod
    def _guess_ext(url: str) -> str:
        clean = url.split("?", 1)[0]
        suffix = clean.rsplit(".", 1)[-1].lower() if "." in clean else "jpg"
        return suffix if suffix in {"jpg", "jpeg", "png", "gif", "webp"} else "jpg"
