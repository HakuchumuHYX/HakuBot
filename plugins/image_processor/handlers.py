from functools import partial

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.rule import to_me

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from utils.files import safe_delete_file
from utils.onebot.media import image_segment
from utils.onebot.messages import reply_image_segments
from utils.onebot.rules import command_boundary
from plugins.image_processor.gif_reverse import reverse_gif
from plugins.image_processor.gif_speed import change_gif_speed
from plugins.image_processor.image_cutout import remove_background
from plugins.image_processor.image_mirror import process_image_mirror
from plugins.image_processor.image_rotate import process_image_rotate
from plugins.image_processor.image_symmetry import process_image_symmetry
from plugins.image_processor.utils import UserInputError
from plugins.image_processor.video_to_gif import convert_video_to_gif

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".webm", ".mkv", ".flv", ".wmv")


def _command(name: str, feature: str):
    return on_command(
        name,
        rule=to_me() & command_boundary & switch_rule(f"image_processor:{feature}"),
        priority=5,
        block=True,
    )


gif_speed_handler = _command("imgx", "speed")
video_to_gif_handler = _command("imggif", "video_to_gif")

SYMMETRY_COMMANDS = {
    "img对称": ("left", "左对称"),
    "img左对称": ("left", "左对称"),
    "img右对称": ("right", "右对称"),
    "img中心对称": ("center", "中心对称"),
    "img上对称": ("top", "上对称"),
    "img下对称": ("bottom", "下对称"),
}
MIRROR_COMMANDS = {
    "img镜像": ("horizontal", "水平镜像"),
    "img上镜像": ("vertical", "垂直镜像"),
}
ROTATE_COMMANDS = {
    "img旋转": ("clockwise", "顺时针旋转"),
    "img顺时针": ("clockwise", "顺时针旋转"),
    "img逆时针": ("counter_clockwise", "逆时针旋转"),
}


async def _check_cooldown(matcher, event: MessageEvent, feature: str, label: str):
    remaining = cooldown_remaining(f"image_processor:{feature}", event)
    if remaining > 0:
        await matcher.finish(f"{label}功能还在冷却中，请等待 {remaining} 秒")


async def _reply_image_url(matcher, event: MessageEvent, label: str) -> str:
    if not event.reply:
        await matcher.finish(f"请回复一条图片消息来使用{label}功能")
    for segment in reply_image_segments(event):
        if url := segment.data.get("url"):
            return url
    await matcher.finish("回复的消息中没有找到图片")


async def _run_job(matcher, event: MessageEvent, feature: str, label: str, job):
    """发处理中提示 → 执行 → 发图 → 记 CD；job 抛出的异常文案直接回给用户"""
    await matcher.send(f"正在处理{label}，请稍候...")
    result_path = ""
    try:
        result_path = await job()
        await matcher.send(image_segment(result_path))
    except UserInputError as e:
        logger.info(f"{label}未处理（素材不合要求）: {e}")
        await matcher.finish(f"{label}失败：{e}")
    except Exception as e:
        logger.exception(f"{label}失败")
        await matcher.finish(f"{label}失败：{e}")
    finally:
        safe_delete_file(result_path)
    mark_cooldown(f"image_processor:{feature}", event)


async def _handle_image(
    matcher, event: MessageEvent, feature: str, label: str, process
):
    await _check_cooldown(matcher, event, feature, label)
    url = await _reply_image_url(matcher, event, label)
    await _run_job(matcher, event, feature, label, lambda: process(url))


def _register_image_command(command: str, feature: str, label: str, process):
    """回复一张图、无参数的命令"""
    matcher = _command(command, feature)

    @matcher.handle()
    async def _(event: MessageEvent):
        await _handle_image(matcher, event, feature, label, process)


_register_image_command("img倒放", "reverse", "GIF倒放", reverse_gif)
_register_image_command("imgcut", "cutout", "抠图", remove_background)
for command, (symmetry_type, label) in SYMMETRY_COMMANDS.items():
    _register_image_command(
        command,
        "symmetry",
        label,
        partial(process_image_symmetry, symmetry_type=symmetry_type),
    )
for command, (direction, label) in MIRROR_COMMANDS.items():
    _register_image_command(
        command, "mirror", label, partial(process_image_mirror, direction=direction)
    )


@gif_speed_handler.handle()
async def handle_gif_speed(event: MessageEvent, cmd_arg: Message = CommandArg()):
    await _check_cooldown(gif_speed_handler, event, "speed", "GIF倍速")

    args = cmd_arg.extract_plain_text().strip()
    if not args:
        await gif_speed_handler.finish("请指定倍速倍数，例如：imgx 2")
    try:
        speed = float(args)
    except ValueError:
        await gif_speed_handler.finish("请输入有效的数字作为倍速倍数")
    if speed <= 0:
        await gif_speed_handler.finish("倍速倍数必须大于0")
    if speed > 5:
        speed = 5.0
        await gif_speed_handler.send("倍速倍数最高为5，已自动调整为5倍速")

    url = await _reply_image_url(gif_speed_handler, event, "GIF倍速")
    await _run_job(
        gif_speed_handler,
        event,
        "speed",
        f"GIF {speed:g} 倍速",
        lambda: change_gif_speed(url, speed),
    )


def _register_rotate(command: str, direction: str, action_name: str):
    matcher = _command(command, "rotate")

    @matcher.handle()
    async def _(event: MessageEvent, cmd_arg: Message = CommandArg()):
        await _check_cooldown(matcher, event, "rotate", "旋转")

        args = cmd_arg.extract_plain_text().strip()
        speed = 1.0
        if args:
            try:
                speed = float(args)
            except ValueError:
                await matcher.finish("请输入有效的数字作为倍速，例如：img旋转 2")
        if speed > 5:
            speed = 5.0
            await matcher.send("转速太快啦！最高只能5倍速哦，已自动调整。")
        elif speed < 0.1:
            speed = 0.1
            await matcher.send("转速太慢啦！最低0.1倍速，已自动调整。")

        url = await _reply_image_url(matcher, event, action_name)
        await _run_job(
            matcher,
            event,
            "rotate",
            f"{action_name}（{speed:g} 倍速）",
            lambda: process_image_rotate(url, direction, speed),
        )


for command, (direction, action_name) in ROTATE_COMMANDS.items():
    _register_rotate(command, direction, action_name)


@video_to_gif_handler.handle()
async def handle_video_to_gif(bot: Bot, event: MessageEvent):
    await _check_cooldown(video_to_gif_handler, event, "video_to_gif", "视频转GIF")
    if not event.reply:
        await video_to_gif_handler.finish("请回复一条视频消息来使用视频转GIF功能")

    video_url, file_name, file_size = "", "", 0
    for segment in event.reply.message:
        data = segment.data
        if segment.type == "video" and data.get("url"):
            video_url = data["url"]
            file_name = data.get("file", "")
            file_size = int(data.get("file_size") or 0)
            break
        if segment.type == "file" and data.get("file", "").lower().endswith(
            VIDEO_EXTENSIONS
        ):
            video_url = data.get("url", "")
            file_name = data["file"]
            file_size = int(data.get("file_size") or 0)
            # 群文件有 file_id 时优先用 API 换取下载链接
            if data.get("file_id") and isinstance(event, GroupMessageEvent):
                try:
                    response = await bot.call_api(
                        "get_group_file_url",
                        group_id=event.group_id,
                        file_id=data["file_id"],
                    )
                except Exception as e:
                    logger.exception("get_group_file_url 失败")
                    await video_to_gif_handler.finish(
                        f"获取群文件下载链接失败: {e}。请确保Bot具有群文件权限。"
                    )
                video_url = response.get("url", "")
            break

    if not video_url:
        if file_name:
            await video_to_gif_handler.finish(
                f"检测到视频文件「{file_name}」，但没能拿到下载链接"
            )
        await video_to_gif_handler.finish(
            "回复的消息中没有找到视频，请确保回复的是视频消息。"
            "\n支持格式: MP4, AVI, MOV, WebM, MKV, FLV, WMV"
        )

    await _run_job(
        video_to_gif_handler,
        event,
        "video_to_gif",
        "视频转GIF",
        lambda: convert_video_to_gif(video_url, file_name, file_size),
    )
