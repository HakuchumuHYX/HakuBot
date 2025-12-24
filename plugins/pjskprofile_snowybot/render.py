import asyncio
from playwright.async_api import async_playwright
from nonebot.log import logger

from .config import plugin_config


async def render_profile(url: str) -> bytes:
    """
    访问 URL，隐藏原有页脚，注入自定义水印并截图
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )

        context = await browser.new_context(
            viewport={"width": 1080, "height": 1920},
            device_scale_factor=2
        )

        page = await context.new_page()

        try:
            logger.info(f"正在加载 PJSK 页面: {url}")
            await page.goto(url)

            await page.wait_for_selector(".main-card", timeout=30000)

            await page.wait_for_load_state("networkidle")

            watermark_text = plugin_config.watermark.replace("\n", "<br>")

            await page.evaluate(f"""() => {{
                const adCard = document.querySelector('.announcement-card');
                if (adCard) adCard.style.display = 'none';

                const originalFooter = document.querySelector('.footer-info');
                if (originalFooter) originalFooter.style.display = 'none';

                const container = document.querySelector('.pjsk-container');
                if (container) {{
                    const wmDiv = document.createElement('div');
                    wmDiv.style.textAlign = 'center';
                    wmDiv.style.color = '#bb6688'; 
                    wmDiv.style.opacity = '0.7';
                    wmDiv.style.fontSize = '14px';
                    wmDiv.style.fontWeight = 'bold';
                    wmDiv.style.marginTop = '20px';
                    wmDiv.style.paddingBottom = '30px';
                    wmDiv.style.fontFamily = 'sans-serif';
                    wmDiv.style.lineHeight = '1.5';
                    wmDiv.innerHTML = `{watermark_text}`;
                    container.appendChild(wmDiv);
                }}
            }}""")

            target_locator = page.locator(".app")

            # 短暂等待渲染刷新
            await asyncio.sleep(0.5)

            # 截图
            img_bytes = await target_locator.screenshot(type="jpeg", quality=90)
            return img_bytes

        except Exception as e:
            logger.error(f"截图失败: {e}")
            raise e
        finally:
            await browser.close()
