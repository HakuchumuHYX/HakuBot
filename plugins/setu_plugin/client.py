from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Any, Dict

import aiohttp

from utils.network.http import download_bytes, get_client_session, get_effective_proxy


_LOLICON_URL = "https://api.lolicon.app/setu/v2"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


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

    async def _request_json(
        self, url: str, *, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        async with get_client_session().get(
            url,
            params=params,
            headers=_HEADERS,
            proxy=get_effective_proxy(self._proxy),
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

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
        Download image bytes locally so the shared image sender can avoid adapter-side downloads.

        NapCat 在发送合并转发时会自行下载 URL；这里由 bot 下载后交给共享文件发送层。
        """
        return await download_bytes(
            url,
            proxy=self._proxy,
            headers=_HEADERS,
            max_bytes=max_bytes,
            timeout=self._timeout,
            attempts=retries + 1,
        )

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
