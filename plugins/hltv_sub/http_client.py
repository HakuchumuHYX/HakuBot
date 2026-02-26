"""
HLTV HTTP 客户端（负责请求、重试、代理、会话管理）
"""

from __future__ import annotations

import asyncio
from typing import Optional

from curl_cffi.requests import AsyncSession
from nonebot.log import logger


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

    async def _get_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate=self._impersonate)
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_proxy(self) -> Optional[str]:
        if self._proxy_list:
            return self._proxy_list[0]
        return None

    async def fetch(self, url: str, max_retries: int = 5) -> Optional[str]:
        """发送请求获取 HTML"""
        session = await self._get_session()
        proxy = self._get_proxy()

        for attempt in range(max_retries):
            try:
                # 添加随机延迟（重试时）
                if attempt > 0:
                    delay = self._min_delay + (attempt * 2)
                    logger.info(f"[HLTV] 重试 {attempt + 1}/{max_retries}，延迟 {delay}s...")
                    await asyncio.sleep(delay)

                logger.info(f"[HLTV] 正在请求: {url}")

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

                if response.status_code == 200:
                    logger.debug(f"[HLTV] 请求成功: {url}")
                    return response.text
                if response.status_code == 403:
                    logger.warning(f"[HLTV] 403 Forbidden: {url}")
                    continue

                logger.warning(f"[HLTV] HTTP {response.status_code}: {url}")
            except Exception as e:
                logger.error(f"[HLTV] 请求失败: {e}")
                continue

        logger.error(f"[HLTV] 请求失败，已达最大重试次数: {url}")
        return None
