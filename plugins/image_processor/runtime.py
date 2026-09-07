from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown
from nonebot import on_command, on_message, get_driver
from nonebot.rule import to_me
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import (
    MessageSegment,
    GroupMessageEvent,
    PrivateMessageEvent,
    Bot,
)
from nonebot.exception import FinishedException
from nonebot.log import logger
import asyncio
import aiohttp
from PIL import Image
import tempfile
import os
from plugins.image_processor.gif_reverse import reverse_gif, reverse_gif_alternative
from plugins.image_processor.image_cutout import remove_background
from plugins.image_processor.gif_speed import (
    change_gif_speed,
    change_gif_speed_alternative,
)
from plugins.image_processor.image_symmetry import process_image_symmetry
from plugins.image_processor.help import HELP
from utils.onebot.help import send_help
from plugins.image_processor.video_to_gif import convert_video_to_gif
from plugins.image_processor.image_mirror import process_image_mirror
from plugins.image_processor.image_rotate import process_image_rotate
from core.access import (
    get_status_override,
    get_plugin_feature_keys,
    sync_feature_statuses,
    is_plugin_enabled,
    set_plugin_status,
    is_feature_enabled,
    set_feature_status,
)
from core.cooldown import get_plugin_cd_duration, check_cd, update_cd, set_plugin_cd
from utils.onebot.media import image_segment
from utils.concurrency import run_in_pool
from utils.files import safe_delete_file


async def _send_generated_image(
    handler, result_path: str, abnormal_message: str, failure_message: str
) -> bool:
    try:
        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
                await handler.send(image_segment(result_path))
                return True
            await handler.send(abnormal_message)
            return False
        await handler.send(failure_message)
        return False
    finally:
        await safe_delete_file(result_path)


driver = get_driver()


@on_plugin_startup(driver, "image_processor")
async def _image_processor_prefetch_rembg_models() -> None:
    """
    启动时确保 rembg 模型完整可用（默认严格模式：下载失败会阻止 bot 启动）。
    可用环境变量：
      - HAKUBOT_REMBG_PREFETCH_STRICT=1/0 （默认 1）
      - HAKUBOT_REMBG_PREFETCH_TIMEOUT=300 （秒，默认 300）
      - HAKUBOT_REMBG_PREFETCH_RETRIES=8 （默认 8）
    """
    try:
        from plugins.image_processor.rembg_prefetch import (
            ensure_rembg_models_downloaded,
        )
        from plugins.image_processor.image_cutout import (
            REMBG_MODEL_PRIMARY,
            REMBG_MODEL_FALLBACK,
        )

        strict_env = os.getenv("HAKUBOT_REMBG_PREFETCH_STRICT", "1").strip().lower()
        strict = strict_env not in ("0", "false", "no", "off")

        timeout_sec = int(os.getenv("HAKUBOT_REMBG_PREFETCH_TIMEOUT", "300"))
        retries = int(os.getenv("HAKUBOT_REMBG_PREFETCH_RETRIES", "8"))

        logger.info(
            f"[rembg] 启动预检查模型: primary={REMBG_MODEL_PRIMARY}, fallback={REMBG_MODEL_FALLBACK}, "
            f"strict={strict}, timeout={timeout_sec}s, retries={retries}"
        )

        await ensure_rembg_models_downloaded(
            [REMBG_MODEL_PRIMARY, REMBG_MODEL_FALLBACK],
            timeout_sec=timeout_sec,
            retries=retries,
            strict=strict,
        )

    except Exception as e:
        # strict=True 时，ensure_rembg_models_downloaded 会抛异常；这里继续抛出以阻止启动
        runtime.health["image_processor:cutout"] = f"unavailable: {type(e).__name__}"
        logger.exception(f"[rembg] 启动预下载/校验模型失败: {e}")
        return


async def download_and_check_gif(url: str) -> bool:
    """下载并检查是否为GIF"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    # 只下载前几KB来判断文件类型
                    content = await response.content.read(1024)

                    # 检查GIF文件头
                    if content.startswith(b"GIF8"):
                        return True

                    # 检查Content-Type
                    content_type = response.headers.get("Content-Type", "")
                    if "gif" in content_type.lower():
                        return True

                    # 检查文件扩展名
                    if any(
                        url.lower().endswith(ext) for ext in [".gif", ".gif?", ".gif&"]
                    ):
                        return True
        return False
    except Exception as e:
        logger.error(f"GIF检测错误: {e}")
        # 如果检测失败，假设是GIF让用户尝试
        return True


async def is_gif_image(url: str) -> bool:
    """更可靠的GIF检测"""
    # 首先检查URL中的扩展名
    if any(url.lower().endswith(ext) for ext in [".gif", ".gif?", ".gif&"]):
        return True

    # 检查文件路径（如果有）
    if "gif" in url.lower():
        return True

    # 最后下载验证
    return await download_and_check_gif(url)
