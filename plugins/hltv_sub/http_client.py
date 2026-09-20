"""HLTV 的共享会话、请求节流、页面缓存与拦截暂停。"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from nonebot.log import logger

from utils.json_io import atomic_write_json, load_json


class HLTVFetchError(Exception):
    def __init__(self, reason: str, *, retry_at: float = 0):
        self.reason = reason
        self.retry_at = retry_at
        message = f"HLTV 暂时无法访问（{reason}）"
        if retry_at:
            when = datetime.fromtimestamp(retry_at, timezone.utc).astimezone(
                timezone(timedelta(hours=8))
            )
            message = f"HLTV 已暂停访问（{reason}），下次允许尝试：{when:%m-%d %H:%M:%S}（北京时间）"
        super().__init__(message)


@dataclass
class FetchResult:
    text: str
    final_url: str = ""


class HLTVHttpClient:
    def __init__(
        self,
        *,
        timeout: int,
        request_interval: float,
        endpoint: str,
        session_name: str,
        cooldown: int,
        max_cooldown: int,
        state_path: Path,
    ) -> None:
        self._timeout = timeout
        self._interval = request_interval
        self._endpoint = endpoint
        self._session_name = session_name
        self._cooldown = cooldown
        self._max_cooldown = max_cooldown
        self._state_path = state_path
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._request_count = 0
        self._cache: OrderedDict[str, tuple[float, FetchResult]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._probe: asyncio.Task | None = None
        self._closed = False
        state = load_json(state_path, missing_ok=True, default={})
        if not state.success:
            raise ValueError("HLTV 暂停状态文件无法读取")
        self._blocked_until = state.data.get("blocked_until", 0.0)
        self._blocks = state.data.get("blocks", 0)

    def _save_state(self) -> None:
        atomic_write_json(
            self._state_path,
            {"blocked_until": self._blocked_until, "blocks": self._blocks},
        )

    def _check_pause(self) -> None:
        if time.time() < self._blocked_until:
            raise HLTVFetchError("访问冷却中", retry_at=self._blocked_until)
        if self._probe is not None and self._probe is not asyncio.current_task():
            raise HLTVFetchError("恢复探测中")

    def _pause(self, reason: str, *, blocked: bool) -> HLTVFetchError:
        delay = 60
        if blocked:
            self._blocks += 1
            delay = min(
                self._cooldown * 2 ** min(self._blocks - 1, 20), self._max_cooldown
            )
        self._blocked_until = time.time() + delay
        self._save_state()
        logger.warning(f"[HLTV] 暂停访问 reason={reason} seconds={delay}")
        return HLTVFetchError(reason, retry_at=self._blocked_until)

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._cache.clear()

    async def fetch_with_meta(
        self,
        url: str,
        *,
        cache_key: str,
        ttl: int,
        force_refresh: bool = False,
    ) -> FetchResult:
        if self._closed:
            raise HLTVFetchError("客户端已关闭")
        cached = self._cache.get(cache_key)
        if not force_refresh and cached and cached[0] > time.monotonic():
            self._cache.move_to_end(cache_key)
            logger.debug(f"[HLTV] cache_hit page={cache_key}")
            return cached[1]
        if cached and cached[0] <= time.monotonic():
            self._cache.pop(cache_key)
        task = self._inflight.get(cache_key)
        if task is not None:
            return await asyncio.shield(task)
        self._check_pause()
        task = asyncio.create_task(self._fetch_and_cache(url, cache_key, ttl))
        self._inflight[cache_key] = task
        # 到期后的第一个调用占用探测权，避免同时排入其他页面。
        if self._blocked_until:
            self._probe = task
        task.add_done_callback(lambda done: self._finish(cache_key, done))
        return await asyncio.shield(task)

    def _finish(self, key: str, task: asyncio.Task) -> None:
        self._inflight.pop(key, None)
        if self._probe is task:
            self._probe = None
        if not task.cancelled():
            task.exception()

    async def _fetch_and_cache(self, url: str, key: str, ttl: int) -> FetchResult:
        async with self._lock:
            self._check_pause()
            result = await self._request(url, key)
            if self._blocked_until:
                self._blocks = 0
                self._blocked_until = 0
                self._save_state()
                logger.info("[HLTV] 恢复访问")
            self._cache[key] = (time.monotonic() + ttl, result)
            self._cache.move_to_end(key)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return result

    async def _call(self, command: str, **params) -> dict:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout + 30, trust_env=False
            )
        try:
            response = await self._client.post(
                self._endpoint,
                json={"cmd": command, "session": self._session_name, **params},
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise self._pause("FlareSolverr 连接失败或响应无效", blocked=False) from None

        if not isinstance(data, dict):
            raise self._pause("FlareSolverr 响应无效", blocked=False)
        if data.get("status") != "ok":
            # 服务异常可能含 URL、代理凭据或浏览器堆栈，仅输出归类后的原因。
            message = str(data.get("message", "")).lower()
            blocked = command == "request.get" and any(
                marker in message
                for marker in (
                    "timeout after",
                    "captcha detected",
                    "challenge not solved",
                    "cloudflare has blocked",
                    "access denied",
                )
            )
            reason = "浏览器挑战未通过" if blocked else "FlareSolverr 服务或浏览器异常"
            raise self._pause(reason, blocked=blocked)
        if not response.is_success:
            raise self._pause("FlareSolverr API 请求失败", blocked=False)
        return data

    async def _request(self, url: str, key: str) -> FetchResult:
        session = await self._call("sessions.create")
        reused = session.get("message") == "Session already exists."
        logger.info(f"[HLTV] browser_session reused={reused}")

        delay = self._last_request + self._interval - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._check_pause()
        self._last_request = time.monotonic()
        self._request_count += 1
        logger.info(f"[HLTV] request page={key} count={self._request_count}")
        data = await self._call(
            "request.get", url=url, maxTimeout=self._timeout * 1000
        )
        solution = data.get("solution")
        if not isinstance(solution, dict):
            raise self._pause("FlareSolverr 页面响应无效", blocked=False)
        html = solution.get("response")
        final_url = solution.get("url")
        if not isinstance(html, str) or not html.strip():
            raise self._pause("页面响应为空", blocked=False)
        if not isinstance(final_url, str):
            raise self._pause("页面地址缺失", blocked=False)
        try:
            parts = urlsplit(final_url)
        except ValueError:
            raise self._pause("页面地址无效", blocked=False) from None
        if parts.scheme != "https" or parts.hostname != "www.hltv.org":
            raise self._pause("页面跳转到了非 HLTV 地址", blocked=False)

        # FlareSolverr 的 solution.status 固定为 200，必须检查实际页面。
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
        heading = soup.h1.get_text(" ", strip=True).lower() if soup.h1 else ""
        blocked = any(
            marker in title or marker in heading
            for marker in (
                "just a moment", "access denied", "sorry, you have been blocked",
            )
        ) or soup.select_one(
            "#challenge-form, #cf-challenge-running, #cf-please-wait, "
            "#challenge-spinner, #turnstile-wrapper, .cf-error-details"
        ) is not None
        if blocked:
            raise self._pause("页面仍为挑战或拒绝访问页", blocked=True)
        if "hltv.org" not in title or any(
            marker in title for marker in ("not found", "server error", "unavailable")
        ):
            raise self._pause("无法识别 HLTV 页面", blocked=False)
        logger.info(
            f"[HLTV] response page={key} "
            f"elapsed={time.monotonic() - self._last_request:.2f}s"
        )
        return FetchResult(text=html, final_url=final_url)

    async def fetch(
        self, url: str, *, cache_key: str, ttl: int, force_refresh: bool = False
    ) -> str:
        result = await self.fetch_with_meta(
            url, cache_key=cache_key, ttl=ttl, force_refresh=force_refresh
        )
        return result.text
