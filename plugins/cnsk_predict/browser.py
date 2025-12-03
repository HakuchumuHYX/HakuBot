from nonebot import require
from nonebot.log import logger

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import get_new_page

try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None
    logger.warning("未检测到 playwright-stealth 库，将在无伪装模式下运行。")


async def manual_capture_page(url: str, dark_mode: bool = False, viewport: dict = None,
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

        # === 核心修改：V6 完美版 CSS ===
        if dark_mode:
            logger.debug("正在注入 V6 完美版 CSS...")
            await page.add_style_tag(content="""
                /* 1. 全局背景深灰 */
                html, body {
                    background-color: #181a1b !important;
                    color: #e8e6e3 !important;
                }

                /* 2. 卡片背景与边框 */
                .card, .kline-card, .movers-section {
                    background-color: #242526 !important;
                    border: 1px solid #3c3e40 !important;
                    box-shadow: none !important;
                    color: #e8e6e3 !important;
                }

                /* 3. 移除卡片头部的白色渐变 */
                .card-header {
                    background: transparent !important;
                    border-bottom: 1px solid #3c3e40 !important;
                }

                /* 4. 修复预测值颜色 (粉色) */
                /* 提高权重，确保粉色不被全局白色覆盖 */
                span.stat-value.predict, .stat-value.predict {
                    color: #FF6699 !important;
                    -webkit-text-fill-color: #FF6699 !important; /* 强制填充颜色 */
                }

                /* 修复涨跌幅颜色 (保持原色) */
                .color-up { color: #f6465d !important; }
                .color-down { color: #0ecb81 !important; }

                /* 5. 通用文字颜色修复 (浅灰) */
                .stat-label, .kline-sub, .movers-head, .mc-rank {
                    color: #a8a095 !important;
                }
                /* 数值高亮 (亮白) */
                .stat-value, .current-index, .mc-val {
                    color: #e8e6e3 !important;
                }

                /* 6. 图表处理 (关键修改：取消反色！) */
                /* 原图表的坐标轴线是浅色(#eee)，在深色背景下刚好可见。
                   取消滤镜后，红色不再偏色，坐标轴也能显示为原本的浅白色。
                */
                canvas {
                    filter: none !important;
                }

                /* 7. 确保图表容器背景透明 */
                /* 这一步至关重要，让深色卡片背景透出来衬托浅色坐标轴 */
                .chart-box, #kline-chart, .kline-card > div {
                    background: transparent !important;
                    background-color: transparent !important;
                }

                /* 8. 迷你卡片微调 */
                .mini-card {
                    background-color: #2c2e30 !important;
                    border: 1px solid #3c3e40 !important;
                }

                /* 9. 隐藏加载动画 */
                .loading-overlay { display: none !important; }
            """)

        try:
            loading_selector = ".loading-text"
            if await page.locator(loading_selector).is_visible():
                await page.locator(loading_selector).wait_for(state="hidden", timeout=15000)

            await page.wait_for_selector("canvas", state="visible", timeout=5000)
            await page.wait_for_timeout(800)
        except Exception as e:
            logger.warning(f"智能等待异常: {e}")

        return await page.screenshot(full_page=True, type="jpeg", quality=85)