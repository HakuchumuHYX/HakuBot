from nonebot.log import logger

from ..utils.browser import get_new_page

try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None
    logger.warning("未检测到 playwright-stealth 库，将在无伪装模式下运行。")


async def manual_capture_page(url: str, viewport: dict = None,
                              device_scale_factor: float = 2.0, timeout: int = 30000) -> bytes:
    if viewport is None:
        viewport = {"width": 640, "height": 1080}

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async with get_new_page(
            device_scale_factor=device_scale_factor,
            viewport=viewport,
            user_agent=user_agent,
            is_mobile=True,
            has_touch=True
    ) as page:
        if Stealth:
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)

        for _ in range(10):
            title = await page.title()
            if any(k in title for k in ["Just a moment", "Verify", "Cloudflare"]):
                await page.wait_for_timeout(1000)
            else:
                break

        # === 等待页面渲染完成（适配 snowyviewer prediction） ===
        try:
            await page.wait_for_selector('h1:has-text("活动")', state="visible", timeout=10000)
        except Exception:
            # 新页面结构变动时，回退为固定缓冲等待，避免刷 warning 日志
            pass

        await page.wait_for_timeout(1200)
        return await page.screenshot(full_page=True, type="jpeg", quality=85)
