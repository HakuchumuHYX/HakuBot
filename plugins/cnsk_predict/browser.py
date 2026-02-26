from nonebot.log import logger

from ..utils.browser import get_new_page

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

        # === 暗色模式 CSS（适配 simple 页面） ===
        if dark_mode:
            logger.debug("正在注入暗色模式 CSS（simple 页面版）...")
            await page.add_style_tag(content="""
                /* 1. 全局背景深灰，覆盖点阵背景 */
                html, body {
                    background-color: #181a1b !important;
                    background-image: none !important;
                    color: #e8e6e3 !important;
                }

                /* 2. 市场概览卡片 */
                .market-inner {
                    background-color: #242526 !important;
                    border: 1px solid #3c3e40 !important;
                    box-shadow: none !important;
                }
                .market-info {
                    background: #1e1f20 !important;
                    border-right: 1px solid #3c3e40 !important;
                }
                .mi-title, .mi-sub {
                    color: #a8a095 !important;
                }
                .mi-value {
                    color: #e8e6e3 !important;
                }

                /* 3. 涨跌排行卡片 */
                .movers-col {
                    background-color: #242526 !important;
                    border: 1px solid #3c3e40 !important;
                    box-shadow: none !important;
                }
                .movers-head {
                    color: #a8a095 !important;
                }

                /* 4. 迷你卡片 */
                .mini-card {
                    background-color: #2c2e30 !important;
                }
                .mini-card.bg-up-light {
                    background-color: rgba(246, 70, 93, 0.1) !important;
                }
                .mini-card.bg-down-light {
                    background-color: rgba(14, 203, 129, 0.1) !important;
                }
                .mc-rank {
                    color: #999 !important;
                }
                .mc-val {
                    color: #e8e6e3 !important;
                }
                .mc-speed {
                    color: #777 !important;
                }

                /* 5. 排名卡片 */
                .rank-card {
                    background-color: #242526 !important;
                    border: 1px solid #3c3e40 !important;
                    box-shadow: none !important;
                }
                .col-rank {
                    background: #1e1f20 !important;
                    border-left: 4px solid var(--p-teal) !important;
                }
                .rank-label {
                    color: #a8a095 !important;
                }
                .rank-num {
                    color: #e8e6e3 !important;
                }
                .score-main {
                    color: #e8e6e3 !important;
                }

                /* 6. 预测值保持粉色 */
                .score-predict-row {
                    color: #FF6699 !important;
                }
                .badge-predict {
                    background: rgba(255, 102, 153, 0.2) !important;
                    color: #FF6699 !important;
                }

                /* 7. 涨跌颜色保持原色 */
                .color-up { color: #f6465d !important; }
                .color-down { color: #0ecb81 !important; }

                /* 8. 图表容器背景透明 */
                .col-chart {
                    background: #1e1f20 !important;
                    border: 1px solid #3c3e40 !important;
                }
                .chart-container, #kline-mini, .market-chart-wrapper {
                    background: transparent !important;
                }
                canvas {
                    filter: none !important;
                }

                /* 9. 底部文字 */
                .footer {
                    color: #666 !important;
                }
                .source-hl {
                    color: #3DD1BF !important;
                }
                .predict-hl {
                    color: #FF6699 !important;
                }
            """)

        # === 等待页面渲染完成 ===
        try:
            # simple 页面无 .loading-text，直接等待 canvas 图表渲染
            await page.wait_for_selector("canvas", state="visible", timeout=5000)
            await page.wait_for_timeout(800)
        except Exception as e:
            logger.warning(f"智能等待异常: {e}")

        return await page.screenshot(full_page=True, type="jpeg", quality=85)
