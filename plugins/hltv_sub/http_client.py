"""
HLTV HTTP 客户端（负责请求、重试、代理、会话管理）
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from curl_cffi.requests import AsyncSession
from nonebot.log import logger


@dataclass
class FetchResult:
    text: Optional[str]
    status_code: Optional[int] = None
    final_url: str = ""
    error: str = ""


class HLTVHttpClient:
    def __init__(
        self,
        *,
        timeout: int,
        min_delay: float,
        proxy_list: list[str] | None = None,
        impersonate: str = "chrome",
    ) -> None:
        self._timeout = timeout
        self._min_delay = min_delay
        self._proxy_list = proxy_list or []
        self._impersonate = impersonate
        self._session: Optional[AsyncSession] = None

        # 代理轮换状态（失败退避 + 冷却）
        self._proxy_cursor: int = 0
        self._proxy_failures: dict[str, int] = {}
        self._proxy_cooldown_until: dict[str, float] = {}
        self._proxy_backoff_base_seconds: int = 3
        self._proxy_backoff_max_seconds: int = 60

    async def _get_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate=self._impersonate)
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _pick_proxy(self) -> Optional[str]:
        """选择当前可用代理（轮换 + 冷却过滤）"""
        if not self._proxy_list:
            return None

        now = time.time()
        candidates = [
            p for p in self._proxy_list if self._proxy_cooldown_until.get(p, 0.0) <= now
        ]
        if not candidates:
            # 全部在冷却中时，允许继续轮换，避免完全阻塞
            candidates = self._proxy_list

        idx = self._proxy_cursor % len(candidates)
        proxy = candidates[idx]
        self._proxy_cursor = (self._proxy_cursor + 1) % max(1, len(candidates))
        return proxy

    def _mark_proxy_failure(self, proxy: Optional[str]) -> None:
        if not proxy:
            return

        failures = self._proxy_failures.get(proxy, 0) + 1
        self._proxy_failures[proxy] = failures

        backoff = min(
            self._proxy_backoff_base_seconds * (2 ** max(0, failures - 1)),
            self._proxy_backoff_max_seconds,
        )
        self._proxy_cooldown_until[proxy] = time.time() + backoff

    def _mark_proxy_success(self, proxy: Optional[str]) -> None:
        if not proxy:
            return
        self._proxy_failures[proxy] = 0
        self._proxy_cooldown_until[proxy] = 0.0

    async def fetch_with_meta(self, url: str, max_retries: int = 5) -> FetchResult:
        """发送请求获取 HTML + 响应元信息"""
        session = await self._get_session()

        last_status: Optional[int] = None
        last_final_url = ""
        last_error = ""

        for attempt in range(max_retries):
            try:
                # 添加随机延迟（重试时）
                if attempt > 0:
                    delay = self._min_delay + (attempt * 2)
                    logger.info(f"[HLTV] 重试 {attempt + 1}/{max_retries}，延迟 {delay}s...")
                    await asyncio.sleep(delay)

                proxy = self._pick_proxy()
                logger.info(f"[HLTV] 正在请求: {url} (proxy={proxy or 'direct'})")

                response = await session.get(
                    url,
                    proxy=proxy,
                    timeout=self._timeout,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                    },
                )

                last_status = response.status_code
                last_final_url = str(response.url)

                if response.status_code == 200:
                    self._mark_proxy_success(proxy)
                    logger.debug(f"[HLTV] 请求成功: {url}")
                    return FetchResult(
                        text=response.text,
                        status_code=response.status_code,
                        final_url=last_final_url,
                    )
                if response.status_code == 403:
                    self._mark_proxy_failure(proxy)
                    logger.warning(f"[HLTV] 403 Forbidden: {url} (proxy={proxy or 'direct'})")
                    continue

                self._mark_proxy_failure(proxy)
                logger.warning(
                    f"[HLTV] HTTP {response.status_code}: {url} (proxy={proxy or 'direct'})"
                )
            except Exception as e:
                last_error = repr(e)
                self._mark_proxy_failure(proxy)
                logger.error(f"[HLTV] 请求失败: {e} (proxy={proxy or 'direct'})")
                continue

        logger.error(f"[HLTV] 请求失败，已达最大重试次数: {url}")
        return FetchResult(
            text=None,
            status_code=last_status,
            final_url=last_final_url,
            error=last_error,
        )

    async def fetch(self, url: str, max_retries: int = 5) -> Optional[str]:
        """发送请求获取 HTML（兼容旧接口）"""
        result = await self.fetch_with_meta(url, max_retries=max_retries)
        return result.text
