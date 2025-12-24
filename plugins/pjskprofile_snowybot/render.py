import asyncio
from playwright.async_api import async_playwright
from nonebot.log import logger

from .config import plugin_config


async def render_profile(url: str) -> bytes:
    """
    访问 URL，隐藏原有页脚，动态获取页面定义的 --theme-color 并注入自定义水印，最后截图
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

            # 等待核心元素加载
            await page.wait_for_selector(".main-card", timeout=30000)

            # 等待网络空闲
            await page.wait_for_load_state("networkidle")

            watermark_text = plugin_config.watermark.replace("\n", "<br>")

            # 执行 JS 修改
            await page.evaluate(f"""() => {{
                // 1. 从 html 标签获取 --theme-color 变量
                const rootStyle = getComputedStyle(document.documentElement);
                const themeColor = rootStyle.getPropertyValue('--theme-color').trim();

                // 2. 隐藏无关元素
                const adCard = document.querySelector('.announcement-card');
                if (adCard) adCard.style.display = 'none';

                const originalFooter = document.querySelector('.footer-info');
                if (originalFooter) originalFooter.style.display = 'none';

                // 3. 注入水印
                const container = document.querySelector('.pjsk-container');
                if (container) {{
                    const wmDiv = document.createElement('div');
                    wmDiv.style.textAlign = 'center';

                    // 使用动态获取的主题色，如果没有获取到则回退到默认色
                    wmDiv.style.color = themeColor || '#bb6688'; 

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

            # 截图范围保持为 .app 以包含顶部装饰条
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
            