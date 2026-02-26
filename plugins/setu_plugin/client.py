from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional, Any, Dict

import httpx


_LOLICON_URL = "https://api.lolicon.app/setu/v2"


@dataclass
class SetuResult:
    """A normalized setu response (first item from lolicon api)."""

    title: str
    pid: int
    url: str
    display_url: str


class SetuClient:
    """
    Minimal lolicon setu client.

    - Fetches image info from https://api.lolicon.app/setu/v2
    - Optionally rewrites image domain via reverse proxy domain.
    """

    def _build_client_kwargs(self) -> Dict[str, Any]:
        client_kwargs: Dict[str, Any] = {
            "timeout": self._timeout,
            "headers": {"User-Agent": "Mozilla/5.0"},
            "follow_redirects": True,
        }

        # httpx 不同版本对代理参数命名不同：proxy / proxies
        if self._proxy:
            client_kwargs["proxy"] = self._proxy

        return client_kwargs

    async def _request_json(self, url: str, *, params: Dict[str, Any]) -> Dict[str, Any]:
        client_kwargs = self._build_client_kwargs()
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
        except TypeError:
            # 回退到旧版参数名 proxies=
            if self._proxy:
                client_kwargs.pop("proxy", None)
                client_kwargs["proxies"] = self._proxy  # type: ignore

            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()

        if not isinstance(payload, dict):
            return {}
        return payload

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        proxy: Optional[str] = None,
        r18: int = 0,
        reverse_proxy_domain: Optional[str] = None,
    ) -> None:
        self._timeout = timeout
        self._proxy = proxy
        self._r18 = r18
        self._reverse_proxy_domain = reverse_proxy_domain

    async def fetch(self, tag: Optional[str] = None) -> Optional[SetuResult]:
        params: Dict[str, Any] = {"r18": self._r18}
        if tag:
            params["tag"] = tag

        payload = await self._request_json(_LOLICON_URL, params=params)

        data = payload.get("data")
        if not data:
            return None

        item = data[0] if isinstance(data, list) and data else None
        if not isinstance(item, dict):
            return None

        title = str(item.get("title") or "")
        pid_raw = item.get("pid") or 0
        try:
            pid = int(pid_raw)
        except Exception:
            pid = 0

        urls = item.get("urls") or {}
        original_url = ""
        regular_url = ""
        if isinstance(urls, dict):
            original_url = str(urls.get("original") or "")
            regular_url = str(urls.get("regular") or "")
        else:
            original_url = ""
            regular_url = ""

        # 发送用图默认选 regular（更小更快），缺失则退回 original
        send_url = regular_url or original_url

        # url 字段保留“原图链接”，display_url 用于实际发送/下载
        url = self._rewrite_domain(original_url) if original_url else ""
        display_url = self._rewrite_domain(send_url) if send_url else ""

        return SetuResult(title=title, pid=pid, url=url, display_url=display_url)

    async def download_image(
        self,
        url: str,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        retries: int = 2,
    ) -> bytes:
        """
        Download image bytes for MessageSegment.image(bytes) to avoid adapter-side TLS/download issues.

        NapCat 在发送“合并转发”时会尝试自行下载 URL 图片；这里改为由 bot 端下载后以 base64 发送，稳定性更高。
        """
        last_exc: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                client_kwargs = self._build_client_kwargs()
                try:
                    async with httpx.AsyncClient(**client_kwargs) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                except TypeError:
                    # 回退到旧版参数名 proxies=
                    if self._proxy:
                        client_kwargs.pop("proxy", None)
                        client_kwargs["proxies"] = self._proxy  # type: ignore
                    async with httpx.AsyncClient(**client_kwargs) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()

                # 简单大小保护
                content_length = resp.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise ValueError(f"image too large: {content_length} bytes")
                    except ValueError:
                        # content-length 非法时忽略
                        pass

                data = resp.content
                if len(data) > max_bytes:
                    raise ValueError(f"image too large: {len(data)} bytes")

                return data
            except Exception as e:
                last_exc = e
                # 简单退避
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

        raise last_exc or RuntimeError("download failed")

    def _rewrite_domain(self, url: str) -> str:
        """
        Rewrite image domain to reverse proxy domain.

        Example:
        - url: https://i.pximg.net/img-original/.../xxx.jpg
        - reverse_proxy_domain: i.pixiv.re
        - result: https://i.pixiv.re/img-original/.../xxx.jpg
        """
        if not self._reverse_proxy_domain:
            return url

        patt = r"://(.*?)/"
        m = re.search(patt, url)
        if not m:
            return url

        domain = m.group(1)
        return url.replace(domain, self._reverse_proxy_domain)
