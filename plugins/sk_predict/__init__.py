from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot, Event
from nonebot.params import CommandArg
from nonebot.log import logger

from ..plugin_manager.enable import is_plugin_enabled
from ..utils.image_utils import path_to_base64_image
from .service import get_cache_file, is_cache_valid, read_cache_age_text, refresh_prediction_cache

from . import scheduler as _scheduler  # noqa: F401


cn_shot_cmd = on_command("cnsk预测", priority=5, block=True)
jp_shot_cmd = on_command("sk预测", priority=5, block=True)


async def handle_predict_command(
    matcher,
    event: Event,
    args: Message,
    *,
    region: str,
    region_name: str,
) -> None:
    if isinstance(event, GroupMessageEvent):
        user_id = str(event.user_id)
        if not is_plugin_enabled("sk_predict", str(event.group_id), user_id):
            await matcher.finish()

    raw_text = args.extract_plain_text().strip()
    force_reload = "reload" in raw_text.split()
    cache_file = get_cache_file(region)

    if is_cache_valid(region, force_reload=force_reload):
        msg_text = (
            f"以下是预测结果（{read_cache_age_text(cache_file)}）\n"
            f"若发现数据过时或图片错误，可在命令后加上 reload 强制刷新：\n"
        )
        await matcher.finish(Message(msg_text) + path_to_base64_image(cache_file))

    if force_reload:
        await matcher.send(f"正在强制刷新 {region_name} 预测数据，请稍候...")
    else:
        await matcher.send(f"正在实时获取 {region_name} 预测数据，请稍候...")

    try:
        img_bytes = await refresh_prediction_cache(region)
    except Exception as e:
        err = str(e)
        logger.exception(f"获取 {region_name} 预测线失败: {err}")
        if cache_file.exists():
            await matcher.finish(
                Message(f"获取最新数据失败 ({err})，显示旧缓存：\n")
                + path_to_base64_image(cache_file)
            )
        await matcher.finish(f"获取失败。\n错误信息: {err}")

    await matcher.finish(MessageSegment.image(img_bytes))


@cn_shot_cmd.handle()
async def handle_cn_predict_command(bot: Bot, event: Event, args: Message = CommandArg()):
    await handle_predict_command(cn_shot_cmd, event, args, region="cn", region_name="CN")


@jp_shot_cmd.handle()
async def handle_jp_predict_command(bot: Bot, event: Event, args: Message = CommandArg()):
    await handle_predict_command(jp_shot_cmd, event, args, region="jp", region_name="JP")
