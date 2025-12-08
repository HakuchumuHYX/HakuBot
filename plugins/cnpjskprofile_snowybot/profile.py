import json
import traceback
import datetime
from pathlib import Path
from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.adapters import Message
from nonebot.log import logger
from nonebot.exception import FinishedException
from .data_manager import get_sc_bind
from .config_manager import load_config

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import get_new_page

try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None
    logger.warning("未检测到 playwright-stealth，将以普通模式运行")

sc_profile = on_command("sc个人信息", aliases={"scpjskprofile"}, priority=5, block=True)

DARK_MODE_CSS = """
/* 1. 全局背景 */
html, body {
    background-color: #1e1e1e !important;
    background-image: none !important;
    min-height: 100vh !important;
}
.top-deco, .bottom-deco, .bg-deco, .footer-deco { display: none !important; }

/* 2. 主容器 */
#mainContainer, .main-card, .section-card, .announcement-card, .music-table {
    background-color: #1e1e1e !important;
    border-color: #333333 !important;
    box-shadow: none !important;
}

/* 3. 歌曲成绩 */
.music-row { border-color: #333333 !important; }
.music-stat-val:not(.fc):not(.ap) { color: #ffffff !important; }
.music-stat-label { color: #cccccc !important; }

/* 4. 个人数据 */
.stat-capsule {
    background-color: #2d2d2d !important;
    border: 1px solid #444 !important;
}
.stat-capsule .capsule-label, 
.stat-capsule .capsule-icon {
    color: #ffffff !important;
}

/* 5. 编队卡片  */
.deck-card {
    background-color: #252525 !important;
    border: 1px solid #444 !important;
    position: relative;
}
#mainContainer .deck-card .chibi-area,
#mainContainer .deck-card .thumb-wrap,
#mainContainer .deck-card .card-info,
#mainContainer .deck-card img {
    background-color: transparent !important;
    background: transparent !important;
    box-shadow: none !important;
}

/* 6. Leader */
.deck-card .leader-ribbon {
    background-color: var(--theme-color, #ff6699) !important;
    color: #ffffff !important;
    text-shadow: 0 1px 2px rgba(0,0,0,0.5);
    z-index: 10;
}
.deck-card.is-leader {
    border: 3px solid var(--theme-color, #ff6699) !important;
    background-color: rgba(255, 255, 255, 0.05) !important;
}

/* 7. 文字清晰度 */
.card-info, .card-stats, .mr-badge, .stars {
    color: #ffffff !important;
    text-shadow: none !important;
}
.card-stats span, .card-stats div { color: #ffffff !important; }

/* 8. Character Rank 区域背景 */
.unit-tab {
    background-color: #252525 !important; /* 深灰色背景 */
    border: 1px solid #444 !important;     /* 增加边框以防背景太黑看不清边界 */
    /* 文字颜色保留原网页的内联样式 (彩色)，在深灰背景上通常可读 */
}
/* 选中状态的 Tab */
.unit-tab.active {
    background-color: #3e3e3e !important;
    color: #ffffff !important;
}

/* 9. 图表 & 图片 */
canvas { filter: invert(100%) hue-rotate(180deg) !important; }
img, svg { filter: none !important; opacity: 1 !important; }

/* 10. 通用文字 */
h1, h2, h3, .user-name, .section-title, .deck-name { color: #ffffff !important; }
.user-id, .user-word, .footer-info { color: #aaaaaa !important; }
"""


@sc_profile.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    user_qq = str(event.user_id)
    bound_id = get_sc_bind(user_qq)

    arg_text = args.extract_plain_text().strip().lower()
    current_hour = datetime.datetime.now().hour

    is_dark_mode = False

    if "dark" in arg_text or "夜间" in arg_text or "深色" in arg_text:
        is_dark_mode = True
        logger.info("调试：强制开启夜间模式")
    elif "light" in arg_text or "日间" in arg_text or "浅色" in arg_text:
        is_dark_mode = False
        logger.info("调试：强制开启日间模式")
    else:
        if current_hour >= 18 or current_hour < 6:
            is_dark_mode = True
            logger.info(f"当前时间 {current_hour}点，自动启用夜间模式")
        else:
            is_dark_mode = False

    if not bound_id:
        await sc_profile.finish("您尚未绑定ID，请发送“sc绑定+ID”进行绑定。")
        return

    config = load_config()
    base_url = config.get("url", "")
    token = config.get("token", "")
    watermark_text = config.get("watermark", "")

    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    if not base_url.endswith("/"):
        base_url = base_url + "/"

    target_url = f"{base_url}{bound_id}?token={token}"

    await sc_profile.send("正在获取数据，请稍候...")

    my_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    try:
        async with get_new_page(viewport={"width": 900, "height": 1440}, device_scale_factor=1.5,
                                user_agent=my_ua) as page:

            if Stealth:
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

            logger.info(f"正在访问: {target_url}")
            await page.goto(target_url, wait_until="commit", timeout=15000)

            try:
                await page.wait_for_selector("#loadingOverlay", state="hidden", timeout=10000)
            except Exception:
                pass

            await page.wait_for_selector("#mainContainer", state="visible", timeout=10000)
            await page.wait_for_selector(".deck-grid", state="visible", timeout=10000)

            logger.info("正在检测图片资源加载状态...")
            await page.wait_for_function("""
                () => {
                    const images = Array.from(document.querySelectorAll('img'));
                    const leaderImg = document.querySelector('#leaderCardImg');
                    if (!leaderImg) return false;
                    const allLoaded = images.every(img => img.complete && img.naturalHeight > 0);
                    return allLoaded && (document.body.innerText.length > 0);
                }
            """, timeout=15000)

            await page.wait_for_selector("#unitChart", state="visible", timeout=10000)
            await page.wait_for_timeout(800)

            if is_dark_mode:
                logger.info("注入夜间模式样式...")
                await page.add_style_tag(content=DARK_MODE_CSS)
                await page.wait_for_timeout(300)

            if watermark_text:
                safe_text = json.dumps(watermark_text)
                wm_color = 'rgba(255, 255, 255, 0.4)' if is_dark_mode else 'rgba(0, 0, 0, 0.3)'

                await page.evaluate(f"""() => {{
                    const div = document.createElement('div');
                    div.style.position = 'fixed';
                    div.style.bottom = '20px';
                    div.style.right = '20px';
                    div.style.zIndex = '9999';
                    div.style.color = '{wm_color}';
                    div.style.fontSize = '22px';
                    div.style.fontWeight = 'bold';
                    div.style.pointerEvents = 'none';
                    div.style.fontFamily = 'Arial, sans-serif';
                    div.style.textAlign = 'right';
                    div.style.whiteSpace = 'pre-wrap';
                    div.style.lineHeight = '1.5';
                    div.innerText = {safe_text};
                    document.body.appendChild(div);
                }}""")

            logger.info("正在截图...")
            pic = await page.screenshot(full_page=True, type="jpeg", quality=90)

        await sc_profile.finish(MessageSegment.image(pic))

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"截图流程出错: {traceback.format_exc()}")
        try:
            if 'page' in locals():
                debug_pic = await page.screenshot(full_page=False, type="jpeg", quality=80)
                await sc_profile.send(MessageSegment.image(debug_pic) + f"\n截图遇到错误 (调试画面):\n{e}")
            else:
                await sc_profile.finish(f"截图失败。\n错误信息: {e}")
        except:
            await sc_profile.finish(f"截图失败且无法保存调试图。\n错误信息: {e}")
