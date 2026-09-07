"""Owned Playwright runtime; a page failure does not close unrelated contexts."""

import asyncio
import os
from utils.paths import project_root
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright


class BrowserPool:
    def __init__(self, max_contexts=5, idle_seconds=300):
        self._slots = asyncio.Semaphore(max_contexts)
        self._lock = asyncio.Lock()
        self._playwright = self._browser = self._idle = None
        self._active = 0
        self._idle_seconds = idle_seconds

    @asynccontextmanager
    async def page(self, *, device_scale_factor=2, **options):
        async with self._slots:
            context = None
            acquired = False
            try:
                async with self._lock:
                    if self._idle is not None:
                        self._idle.cancel()
                        self._idle = None
                    if self._browser is None or not self._browser.is_connected():
                        if self._playwright is None:
                            os.environ.setdefault(
                                "PLAYWRIGHT_BROWSERS_PATH",
                                str(project_root() / "data/shared/playwright"),
                            )
                            self._playwright = await async_playwright().start()
                        self._browser = await self._playwright.chromium.launch(
                            headless=True,
                            args=["--no-sandbox", "--disable-setuid-sandbox"],
                        )
                    self._active += 1
                    acquired = True
                    browser = self._browser
                context = await browser.new_context(
                    device_scale_factor=device_scale_factor, **options
                )
                yield await context.new_page()
            finally:
                try:
                    if context is not None:
                        await context.close()
                finally:
                    if acquired:
                        async with self._lock:
                            self._active -= 1
                            if self._active == 0:
                                self._idle = asyncio.create_task(self._close_idle())

    async def _close_idle(self):
        try:
            await asyncio.sleep(self._idle_seconds)
            async with self._lock:
                if self._active == 0:
                    await self._close_resources()
        except asyncio.CancelledError:
            pass

    async def _close_resources(self):
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    async def close(self):
        if self._idle is not None:
            self._idle.cancel()
            await asyncio.gather(self._idle, return_exceptions=True)
            self._idle = None
        async with self._lock:
            await self._close_resources()


browser_pool = BrowserPool()
