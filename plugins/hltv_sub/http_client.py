"""
HLTV HTTP 客户端（负责请求、重试、代理、会话管理）

改进：
- 使用可配置的 impersonate 版本（默认 chrome136）
- 完善请求头（Sec-* 系列）
- FlareSolverr 回退（遇到持续 403 时自动尝试）
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from curl_cffi.requests import AsyncSession
from nonebot.log import logger


@dataclass
class FetchResult:
    text: Optional[str]
    status_code: Optional[int] = None
    final_url: str = ""
    error: str = ""


def _build_headers() -> dict[str, str]:
    """构建完整的现代 Chrome 浏览器请求头"""
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Sec-Ch-Ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


class HLTVHttpClient:
    def __init__(
        self,
        *,
        timeout: int,
        min_delay: float,
        proxy_list: list[str] | None = None,
        impersonate: str = "chrome136",
        flaresolverr_url: str = "",
    ) -> None:
        self._timeout = timeout
        self._min_delay = min_delay
        self._proxy_list = proxy_list or []
        self._impersonate = impersonate
        self._flaresolverr_url = flaresolverr_url.rstrip("/") if flaresolverr_url else ""
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

    async def _fetch_via_flaresolverr(self, url: str) -> FetchResult:
        """通过 FlareSolverr 获取页面（Cloudflare 挑战回退）"""
        if not self._flaresolverr_url:
            return FetchResult(text=None, error="flaresolverr_not_configured")

        endpoint = f"{self._flaresolverr_url}"
        # 确保 endpoint 以 /v1 结尾
        if not endpoint.endswith("/v1"):
            endpoint = endpoint.rstrip("/") + "/v1"

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self._timeout * 1000,  # FlareSolverr 使用毫秒
        }

        try:
            logger.info(f"[HLTV] FlareSolverr 回退请求: {url}")
            async with httpx.AsyncClient(timeout=self._timeout + 30) as client:
                resp = await client.post(endpoint, json=payload)
                data = resp.json()

            if data.get("status") == "ok":
                solution = data.get("solution", {})
                html = solution.get("response", "")
                status_code = solution.get("status", 200)
                final_url = solution.get("url", url)
                logger.info(
                    f"[HLTV] FlareSolverr 成功: {url} status={status_code}"
                )
                return FetchResult(
                    text=html if html else None,
                    status_code=status_code,
                    final_url=final_url,
                )
            else:
                error_msg = data.get("message", "unknown_error")
                logger.warning(f"[HLTV] FlareSolverr 失败: {url} error={error_msg}")
                return FetchResult(text=None, error=f"flaresolverr: {error_msg}")

        except Exception as e:
            logger.error(f"[HLTV] FlareSolverr 异常: {url} error={e}")
            return FetchResult(text=None, error=f"flaresolverr_exception: {repr(e)}")

    async def fetch_with_meta(self, url: str, max_retries: int = 5) -> FetchResult:
        """发送请求获取 HTML + 响应元信息"""
        session = await self._get_session()
        headers = _build_headers()

        last_status: Optional[int] = None
        last_final_url = ""
        last_error = ""
        consecutive_403 = 0

        for attempt in range(max_retries):
            try:
                # 添加随机延迟（重试时）
                if attempt > 0:
                    delay = self._min_delay + (attempt * 2) + random.uniform(0, 2)
                    logger.info(f"[HLTV] 重试 {attempt + 1}/{max_retries}，延迟 {delay:.1f}s...")
                    await asyncio.sleep(delay)

                proxy = self._pick_proxy()
                logger.info(f"[HLTV] 正在请求: {url} (proxy={proxy or 'direct'}, impersonate={self._impersonate})")

                response = await session.get(
                    url,
                    proxy=proxy,
                    timeout=self._timeout,
                    headers=headers,
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
                    consecutive_403 += 1
                    self._mark_proxy_failure(proxy)
                    logger.warning(f"[HLTV] 403 Forbidden: {url} (proxy={proxy or 'direct'}, attempt={attempt + 1})")

                    # 连续 403 达到 2 次且配置了 FlareSolverr，尝试回退
                    if consecutive_403 >= 2 and self._flaresolverr_url:
                        logger.info(f"[HLTV] 连续 {consecutive_403} 次 403，尝试 FlareSolverr 回退...")
                        fs_result = await self._fetch_via_flaresolverr(url)
                        if fs_result.text:
                            return fs_result
                        logger.warning(f"[HLTV] FlareSolverr 回退失败: {fs_result.error}")

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

        # 所有重试都失败后，最后尝试一次 FlareSolverr
        if self._flaresolverr_url and last_status == 403:
            logger.info(f"[HLTV] 所有重试失败（403），最终 FlareSolverr 回退...")
            fs_result = await self._fetch_via_flaresolverr(url)
            if fs_result.text:
                return fs_result

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
