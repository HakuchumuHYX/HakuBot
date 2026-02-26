import time
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from nonebot import on_command, require, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot, Event
from nonebot.params import CommandArg
from nonebot.log import logger

# === 引入模块 ===
from ..plugin_manager.enable import is_plugin_enabled
from ..utils.image_utils import path_to_base64_image
from .config import load_config, DATA_DIR, CACHE_EXPIRE_SECONDS, FILE_CLEAN_SECONDS, AUTO_REFRESH_INTERVAL
from .browser import manual_capture_page
from .image import add_watermark

# === 引入定时任务 ===
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# === 并发限制 ===
RENDER_SEMAPHORE = asyncio.Semaphore(3)

# 注册命令
shot_cmd = on_command("cnsk预测", priority=5, block=True)


def get_is_dark_mode(mode_override: str = None) -> bool:
    """辅助函数：判断当前是否应该使用夜间模式"""
    if mode_override == "night":
        return True
    elif mode_override == "day":
        return False
    else:
        # 自动判定：晚上19点到次日早上6点为夜间
        current_hour = datetime.now().hour
        return current_hour >= 19 or current_hour < 6


async def generate_screenshot_task(mode_override: str = None) -> Tuple[Optional[bytes], Path, str]:
    """
    核心任务：执行一次截图、处理并保存。
    返回: (图片bytes, 图片路径Path, 错误信息str)
    """
    is_dark_mode = get_is_dark_mode(mode_override)
    suffix = "_dark" if is_dark_mode else ""

    # 获取 Event Loop 读取配置
    loop = asyncio.get_running_loop()
    config = await loop.run_in_executor(None, load_config)

    # 确定 URL 和 路径
    target_url = config.get("url_home", "https://sekaibangdan.exmeaning.com/simple")
    cache_filename = f"home{suffix}.jpg"
    log_name = "主页预测"

    log_name += " [夜间]" if is_dark_mode else " [日间]"
    image_path = DATA_DIR / cache_filename

    error_msg = ""
    final_img_bytes = None

    # 使用信号量防止并发过高炸内存
    async with RENDER_SEMAPHORE:
        try:
            logger.info(f"正在生成: {log_name} ...")
            # 1. 浏览器截图
            raw_img = await manual_capture_page(
                url=target_url,
                dark_mode=is_dark_mode,
                timeout=30000,
            )

            if raw_img:
                # 2. 图像处理 (CPU 密集型 -> 线程池)
                final_img_bytes = await loop.run_in_executor(
                    None,
                    add_watermark,
                    raw_img,
                    config
                )

                # 3. 写入文件 (IO 密集型 -> 线程池)
                await loop.run_in_executor(None, image_path.write_bytes, final_img_bytes)
                logger.info(f"成功保存: {image_path.name}")
            else:
                error_msg = "浏览器截图返回为空"

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[{log_name}] 处理异常: {e}")

    return final_img_bytes, image_path, error_msg


@shot_cmd.handle()
async def handle_screenshot(bot: Bot, event: Event, args: Message = CommandArg()):
    # 插件开关检查
    if isinstance(event, GroupMessageEvent):
        user_id = str(event.user_id)
        if not is_plugin_enabled("cnsk_predict", str(event.group_id), user_id):
            await shot_cmd.finish()

    # === 1. 参数解析 ===
    raw_text = args.extract_plain_text().strip()
    arg_parts = raw_text.split()

    force_reload = False
    mode_override = None

    for part in arg_parts:
        if part == "reload":
            force_reload = True
        elif part == "day":
            mode_override = "day"
        elif part == "night":
            mode_override = "night"

    # === 2. 路径预判 ===
    is_dark = get_is_dark_mode(mode_override)
    suffix = "_dark" if is_dark else ""
    filename = f"home{suffix}.jpg"
    image_path = DATA_DIR / filename

    # === 3. 逻辑分流 ===

    # 情况 A: 用户不强制刷新，且本地有文件 (命中缓存)
    if not force_reload and image_path.exists():
        # 计算文件即时性
        mtime = image_path.stat().st_mtime
        minutes_ago = int((time.time() - mtime) / 60)

        # 构建回复文案
        if minutes_ago < 1:
            time_str = "刚刚"
        else:
            time_str = f"{minutes_ago}分钟前"

        msg_text = f"以下是预测结果（{time_str}）\n若发现数据过时或图片错误，可在命令后加上 reload 获取最新数据：\n"

        await shot_cmd.finish(Message(msg_text) + path_to_base64_image(image_path))

    # 情况 B: 用户强制刷新，或者本地没有文件 (需要现场获取)
    if force_reload:
        await shot_cmd.send("正在强制刷新数据，请稍候...")
    else:
        await shot_cmd.send("本地暂无缓存，正在获取数据...")

    img_bytes, path, err = await generate_screenshot_task(mode_override)

    if img_bytes:
        # 现场获取成功，直接发图
        await shot_cmd.finish(MessageSegment.image(img_bytes))
    elif path.exists():
        # 获取失败，但有旧图（兜底）
        await shot_cmd.finish(
            Message(f"获取最新数据失败 ({err})，显示旧缓存：\n") +
            path_to_base64_image(path)
        )
    else:
        await shot_cmd.finish(f"获取失败。\n错误信息: {err}")


# === 后台自动获取任务 ===
@scheduler.scheduled_job("interval", minutes=AUTO_REFRESH_INTERVAL, id="auto_refresh_cnsk")
async def auto_refresh_job():
    """
    后台定时任务：每隔5分钟自动刷新主页截图
    包含重试机制
    """
    logger.info("开始执行后台自动刷新预测图...")

    retry_count = 2  # 允许重试2次，共尝试3次
    success = False

    for i in range(retry_count + 1):
        img_bytes, path, err = await generate_screenshot_task(mode_override=None)

        if img_bytes:
            success = True
            logger.info(f"后台刷新成功。保存至: {path.name}")
            break
        else:
            logger.warning(f"后台刷新失败 (第 {i + 1} 次尝试): {err}")
            if i < retry_count:
                await asyncio.sleep(5)  # 失败后等待5秒重试

    if not success:
        logger.error("后台刷新任务最终失败，已达最大重试次数。")


# === 定时清理任务 ===
@scheduler.scheduled_job("cron", hour=4, minute=0, id="clean_sekai_cache")
async def clean_cache():
    logger.info("开始清理截图缓存...")
    count = 0
    current_time = time.time()
    for file in DATA_DIR.iterdir():
        if file.is_file() and file.suffix in [".png", ".jpg", ".jpeg"]:
            if current_time - file.stat().st_mtime > FILE_CLEAN_SECONDS:
                try:
                    file.unlink()
                    count += 1
                except Exception:
                    pass
    logger.info(f"缓存清理完成，共删除了 {count} 张过期图片。")


# === 启动时立即执行一次 ===
driver = get_driver()


async def run_init_task():
    # 等待10秒，让浏览器和系统资源准备就绪
    await asyncio.sleep(10)
    logger.info("Bot启动，执行首次预测图预热...")
    await auto_refresh_job()


@driver.on_startup
async def start_prefetch_task():
    # 使用 create_task 创建非阻塞后台任务
    asyncio.create_task(run_init_task())
