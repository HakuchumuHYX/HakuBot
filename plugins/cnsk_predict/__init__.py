import time
import asyncio
from datetime import datetime
from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.log import logger

# === 引入模块 ===
from .config import load_config, DATA_DIR, CACHE_EXPIRE_SECONDS, FILE_CLEAN_SECONDS
from .browser import manual_capture_page
from .image import add_watermark

# === 引入定时任务 ===
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# === 并发限制 ===
RENDER_SEMAPHORE = asyncio.Semaphore(3)

# 注册命令
shot_cmd = on_command("cnsk预测", priority=5, block=True)


@shot_cmd.handle()
async def handle_screenshot(args: Message = CommandArg()):
    # === 1. 参数全解析 (兼容乱序) ===
    raw_text = args.extract_plain_text().strip()
    arg_parts = raw_text.split()

    force_reload = False
    target_id = ""
    mode_override = None  # None=Auto, 'day', 'night'

    # 遍历参数列表进行分类
    for part in arg_parts:
        if part == "reload":
            force_reload = True
        elif part == "day":
            mode_override = "day"
        elif part == "night":
            mode_override = "night"
        elif part.isdigit():
            target_id = part

    # === 2. 判定是否启用夜间模式 ===
    if mode_override == "night":
        is_dark_mode = True
    elif mode_override == "day":
        is_dark_mode = False
    else:
        # 自动判定：晚上19点到次日早上6点为夜间
        current_hour = datetime.now().hour
        is_dark_mode = (current_hour >= 19 or current_hour < 6)

    # 获取 Event Loop
    loop = asyncio.get_running_loop()

    # 异步读取配置
    config = await loop.run_in_executor(None, load_config)

    target_url = ""
    cache_filename = ""
    log_name = ""

    # === 3. 确定目标 URL 和 缓存文件名 ===
    # 缓存文件名需要加上模式后缀，区分日夜间缓存
    suffix = "_dark" if is_dark_mode else ""

    if not target_id:
        target_url = config.get("url_home", "https://sekairanking.exmeaning.com/")
        cache_filename = f"home{suffix}.jpg"
        log_name = "当前活动"
    else:
        target_url = f"{config.get('url_event_prefix', 'https://sekairanking.exmeaning.com/event/')}{target_id}"
        cache_filename = f"{target_id}{suffix}.jpg"
        log_name = f"活动 {target_id}"

    # 增加日志显示模式
    log_name += " [夜间]" if is_dark_mode else " [日间]"

    image_path = DATA_DIR / cache_filename
    current_time = time.time()

    # === 4. 检查缓存 ===
    if not force_reload and image_path.exists():
        file_mtime = image_path.stat().st_mtime
        if current_time - file_mtime < CACHE_EXPIRE_SECONDS:
            logger.info(f"[{log_name}] 命中缓存")
            await shot_cmd.finish(MessageSegment.image(image_path))
        else:
            logger.info(f"[{log_name}] 缓存过期")
    elif force_reload:
        logger.info(f"[{log_name}] 用户请求强制刷新")

    await shot_cmd.send(f"正在获取 [{log_name}]...")

    final_img_bytes = None
    error_msg = ""

    async with RENDER_SEMAPHORE:
        try:
            # === 调用浏览器模块 (传入 dark_mode 参数) ===
            raw_img = await manual_capture_page(
                url=target_url,
                dark_mode=is_dark_mode,  # 核心：控制浏览器是否注入 CSS
                timeout=30000,
            )

            if raw_img:
                # === 图像处理 (CPU 密集型 -> 线程池运行) ===
                final_img_bytes = await loop.run_in_executor(
                    None,
                    add_watermark,
                    raw_img,
                    config
                )

                # === 写入文件 (IO 密集型 -> 线程池运行) ===
                await loop.run_in_executor(None, image_path.write_bytes, final_img_bytes)
            else:
                error_msg = "截图结果为空"

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[{log_name}] 处理失败: {e}")

    # === 5. 发送结果 ===
    if final_img_bytes:
        await shot_cmd.finish(MessageSegment.image(final_img_bytes))

    elif image_path.exists():
        prefix = "强制刷新失败" if force_reload else "网络获取失败"
        await shot_cmd.finish(
            Message(f"{prefix}，显示旧缓存：\n") +
            MessageSegment.image(image_path)
        )
    else:
        await shot_cmd.finish(f"获取失败。\n错误信息: {error_msg}")


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
