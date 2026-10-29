import math

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import Command, CommandArg, CommandWhitespace
from nonebot.rule import to_me

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from utils.files import safe_delete_file
from utils.onebot.media import image_segment
from utils.onebot.messages import reply_image_segments
from utils.onebot.rules import command_boundary
from plugins.image_processor.image_cutout import remove_background
from plugins.image_processor.image_rotate import process_image_rotate
from plugins.image_processor.transforms import (
    change_gif_speed,
    mirror_image,
    reverse_gif,
    symmetry_image,
)
from plugins.image_processor.utils import ProcessingError, UserInputError
from plugins.image_processor.video_to_gif import convert_video_to_gif

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".webm", ".mkv", ".flv", ".wmv")

# 同一功能的几种叫法：命令名 → (处理参数, 文案)，键同时用作 on_command 的别名
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


# imgx、img旋转 的倍数可以连写（imgx5、img旋转2），连写时要求参数以数字开头，
# 否则「imgxxx」「img旋转木马」这类也会触发
async def _speed_arg_rule(
    arg: Message = CommandArg(), whitespace: str | None = CommandWhitespace()
) -> bool:
    if await command_boundary(arg, whitespace):
        return True
    return arg.extract_plain_text()[:1].isdigit()


async def _parse_speed(matcher, text: str, low: float, high: float) -> float:
    """解析倍速，越界钳到 [low, high] 并提示"""
    try:
        speed = float(text)
    except ValueError:
        speed = math.nan
    # float() 认 "nan"、"inf"，而 nan 和谁比较都是 False，范围检查拦不住
    if not math.isfinite(speed):
        await matcher.finish(f"请输入有效的数字作为倍速，范围 {low:g}～{high:g}")
    clamped = min(max(speed, low), high)
    if clamped != speed:
        await matcher.send(f"倍速范围 {low:g}～{high:g}，已自动调整为 {clamped:g} 倍速")
    return clamped


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
    except ProcessingError as e:
        # 下载失败、子进程超时不打 traceback，带上底层原因就够排查
        cause = e.__cause__ or e.__context__
        logger.warning(f"{label}失败: {e}" + (f"（{cause!r}）" if cause else ""))
        await matcher.finish(f"{label}失败：{e}")
    except Exception:
        # 内部异常的原文可能带路径、库内部信息，不回群
        logger.exception(f"{label}失败")
        await matcher.finish(f"{label}失败，请稍后再试")
    finally:
        safe_delete_file(result_path)
    mark_cooldown(f"image_processor:{feature}", event)


async def _handle_image(
    matcher, event: MessageEvent, feature: str, label: str, process
):
    """回复一张图、无参数的命令"""
    await _check_cooldown(matcher, event, feature, label)
    url = await _reply_image_url(matcher, event, label)
    await _run_job(matcher, event, feature, label, lambda: process(url))


reverse_handler = on_command(
    "img倒放",
    rule=to_me() & command_boundary & switch_rule("image_processor:reverse"),
    priority=5,
    block=True,
)


@reverse_handler.handle()
async def handle_reverse(event: MessageEvent):
    await _handle_image(reverse_handler, event, "reverse", "GIF倒放", reverse_gif)


cutout_handler = on_command(
    "imgcut",
    rule=to_me() & command_boundary & switch_rule("image_processor:cutout"),
    priority=5,
    block=True,
)


@cutout_handler.handle()
async def handle_cutout(event: MessageEvent):
    await _handle_image(cutout_handler, event, "cutout", "抠图", remove_background)


symmetry_handler = on_command(
    "img对称",
    aliases=set(SYMMETRY_COMMANDS),
    rule=to_me() & command_boundary & switch_rule("image_processor:symmetry"),
    priority=5,
    block=True,
)


@symmetry_handler.handle()
async def handle_symmetry(event: MessageEvent, cmd: tuple[str, ...] = Command()):
    symmetry_type, label = SYMMETRY_COMMANDS[cmd[0]]
    await _handle_image(
        symmetry_handler,
        event,
        "symmetry",
        label,
        lambda url: symmetry_image(url, symmetry_type),
    )


mirror_handler = on_command(
    "img镜像",
    aliases=set(MIRROR_COMMANDS),
    rule=to_me() & command_boundary & switch_rule("image_processor:mirror"),
    priority=5,
    block=True,
)


@mirror_handler.handle()
async def handle_mirror(event: MessageEvent, cmd: tuple[str, ...] = Command()):
    direction, label = MIRROR_COMMANDS[cmd[0]]
    await _handle_image(
        mirror_handler,
        event,
        "mirror",
        label,
        lambda url: mirror_image(url, direction),
    )


gif_speed_handler = on_command(
    "imgx",
    rule=to_me() & _speed_arg_rule & switch_rule("image_processor:speed"),
    priority=5,
    block=True,
)


@gif_speed_handler.handle()
async def handle_gif_speed(event: MessageEvent, cmd_arg: Message = CommandArg()):
    await _check_cooldown(gif_speed_handler, event, "speed", "GIF倍速")

    args = cmd_arg.extract_plain_text().strip()
    if not args:
        await gif_speed_handler.finish("请指定倍速倍数，例如：imgx 2")
    speed = await _parse_speed(gif_speed_handler, args, 0.1, 5)

    url = await _reply_image_url(gif_speed_handler, event, "GIF倍速")
    await _run_job(
        gif_speed_handler,
        event,
        "speed",
        f"GIF {speed:g} 倍速",
        lambda: change_gif_speed(url, speed),
    )


rotate_handler = on_command(
    "img旋转",
    aliases=set(ROTATE_COMMANDS),
    rule=to_me() & _speed_arg_rule & switch_rule("image_processor:rotate"),
    priority=5,
    block=True,
)


@rotate_handler.handle()
async def handle_rotate(
    event: MessageEvent,
    cmd: tuple[str, ...] = Command(),
    cmd_arg: Message = CommandArg(),
):
    await _check_cooldown(rotate_handler, event, "rotate", "旋转")
    direction, action_name = ROTATE_COMMANDS[cmd[0]]

    args = cmd_arg.extract_plain_text().strip()
    # 下限 0.2：每圈最多 200 帧，再慢也只是同一个效果
    speed = await _parse_speed(rotate_handler, args, 0.2, 5) if args else 1.0

    url = await _reply_image_url(rotate_handler, event, action_name)
    await _run_job(
        rotate_handler,
        event,
        "rotate",
        f"{action_name}（{speed:g} 倍速）",
        lambda: process_image_rotate(url, direction, speed),
    )


video_to_gif_handler = on_command(
    "imggif",
    rule=to_me() & command_boundary & switch_rule("image_processor:video_to_gif"),
    priority=5,
    block=True,
)


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
