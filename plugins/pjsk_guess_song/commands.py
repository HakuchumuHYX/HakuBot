"""群命令：猜歌、猜歌手、答题、听、听anvo、排行榜、资源统计"""

from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    GROUP,
    ActionFailed,
    Bot,
    GroupMessageEvent,
    Message,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from core.access import switch_rule
from utils.json_io import load_json
from utils.onebot.media import audio_segment, image_segment
from utils.onebot.rules import command_boundary
from plugins.pjsk_guess_song import audio, game, render, songs, store
from plugins.pjsk_guess_song.config import VERSION_FILE, settings
from plugins.pjsk_guess_song.songs import Song, Vocal

GAME_RULE = command_boundary & switch_rule("pjsk_guess_song")
LISTEN_RULE = command_boundary & switch_rule("pjsk_guess_song:listen")

VS_WORDS = {"vs", "v", "vocal"}
SEKAI_WORDS = {"s", "sekai"}
VERSION_LABELS = {
    "sekai": "(Sekai Ver.)",
    "virtual_singer": "(Virtual Singer Ver.)",
    "another_vocal": "(Another Vocal)",
}


def _command(name: str, rule, aliases: set[str] | None = None) -> type[Matcher]:
    return on_command(
        name, aliases=aliases, rule=rule, permission=GROUP, priority=5, block=True
    )


# ============== 猜歌 ==============

guess_cmd = _command("猜歌", GAME_RULE, {"gs"})
vocalist_cmd = _command("猜歌手", GAME_RULE)


@guess_cmd.handle()
async def handle_guess(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    await game.start(matcher, bot, event, game.song_question)


@vocalist_cmd.handle()
async def handle_vocalist(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    if not songs.anvo_songs:
        await matcher.finish(
            "......抱歉，没有找到包含 another_vocal 的歌曲，无法开始游戏。"
        )
    await game.start(matcher, bot, event, game.vocalist_question)


answer_matcher = on_message(priority=3, block=False)


@answer_matcher.handle()
async def handle_answer(matcher: Matcher, event: GroupMessageEvent):
    text = event.get_plaintext().strip()
    if not text.isdecimal():
        return
    rnd = game.current(event.group_id)
    if rnd is None:
        return
    # 只有本群确实在答题时才拦下，否则纯数字消息会被吞掉
    matcher.stop_propagation()
    reply = game.guess(rnd, event, int(text))
    if reply:
        await matcher.send(reply, at_sender=True)


# ============== 听歌 ==============


async def _occupy_for_listen(matcher: Matcher, event: GroupMessageEvent) -> bool:
    """检查听歌次数并占住本群；返回 True 时调用方必须 game.release"""
    limit = settings.daily_listen_limit
    if await store.daily_count(str(event.user_id), "", "listen") >= limit:
        await matcher.send(
            f"......你今天听歌的次数已达上限（{limit}次），请明天再来吧......"
        )
        return False
    if message := game.occupy(event.group_id):
        await matcher.send(message)
        return False
    return True


async def _send_track(
    matcher: Matcher, event: GroupMessageEvent, song: Song, vocal: Vocal, label: str
) -> None:
    await matcher.send("正在加载数据……")
    try:
        track = await audio.full_track(vocal.bundle, song.filler_sec)
    except Exception:
        logger.exception(f"听歌：{song.title} 音频处理失败")
        await matcher.send("......音频处理失败了，过会儿再试吧。")
        return
    title = Message(f"歌曲:{song.id}. {song.title} {label}\n")
    title.append(image_segment(songs.jacket_url(song.jacket)))
    await matcher.send(title)
    await matcher.send(audio_segment(track))
    await store.bump_daily(str(event.user_id), "", "listen")


listen_cmd = _command("听", LISTEN_RULE)


@listen_cmd.handle()
async def handle_listen(
    matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()
):
    content = args.extract_plain_text().strip()
    if not content:
        await matcher.finish(
            "......请指定要听的歌曲名称或ID。例如：听 <歌名> [sekai/vs]"
        )

    # 末尾的 vs / sekai 是版本，其他情况整句当歌名
    query, version, fallback = content, "sekai", "virtual_singer"
    parts = content.rsplit(maxsplit=1)
    if len(parts) == 2 and parts[1].lower() in VS_WORDS | SEKAI_WORDS:
        query = parts[0]
        if parts[1].lower() in VS_WORDS:
            version, fallback = fallback, version

    if not await _occupy_for_listen(matcher, event):
        return
    try:
        song = songs.find_song(query)
        if song is None:
            await matcher.send(f"......没有找到与 '{query}' 匹配的歌曲。")
            return
        vocal = (
            song.vocal(version) or song.vocal(fallback) or next(iter(song.vocals), None)
        )
        if vocal is None:
            await matcher.send(
                f'......歌曲 "{song.title}" 没有找到符合要求的音频文件。'
            )
            return
        await _send_track(
            matcher, event, song, vocal, VERSION_LABELS.get(vocal.type, "")
        )
    finally:
        game.release(event.group_id)


listen_anvo_cmd = _command(
    "听anvo", LISTEN_RULE, {"listen_anvo", "listen_anov", "听anov", "anvo", "anov"}
)


def _anvo_versions(song: Song) -> str:
    vocals = song.vocals_of("another_vocal")
    if not vocals:
        return f"......歌曲 '{song.title}' 没有 Another Vocal 版本。"
    lines = [
        f"  - {songs.vocalist_name(v)} "
        f"({'+'.join(songs.CHARACTER_ABBRS.get(c, 'unk') for c in v.character_ids)})"
        for v in vocals
    ]
    return (
        f'歌曲 "{song.title}" 有以下 Another Vocal 版本:\n'
        + "\n".join(lines)
        + f"\n\n请使用 /听anvo {song.id} <角色> 来播放。"
    )


@listen_anvo_cmd.handle()
async def handle_listen_anvo(
    matcher: Matcher, event: GroupMessageEvent, args: Message = CommandArg()
):
    content = args.extract_plain_text().strip()
    if not songs.anvo_songs:
        await matcher.finish("......抱歉，没有找到任何可用的 Another Vocal 歌曲。")

    if not await _occupy_for_listen(matcher, event):
        return
    try:
        song, vocal, listing = songs.parse_anvo(content)
        if song is None:
            await matcher.send(f"......没有找到与 '{content}' 匹配的歌曲或角色。")
        elif listing:
            await matcher.send(_anvo_versions(song))
        elif vocal is None:
            await matcher.send(
                f'......歌曲 "{song.title}" 没有找到符合要求的 Another Vocal 版本。'
            )
        else:
            label = f"(Another Vocal - {songs.vocalist_name(vocal)})"
            await _send_track(matcher, event, song, vocal, label)
    finally:
        game.release(event.group_id)


# ============== 排行榜 / 资源 ==============

leaderboard_cmd = _command("群聊猜歌排行", GAME_RULE, {"猜歌排行", "pjsk排行"})


@leaderboard_cmd.handle()
async def handle_leaderboard(matcher: Matcher, bot: Bot, event: GroupMessageEvent):
    rows = await store.leaderboard(str(event.group_id), limit=5)
    if not rows:
        await matcher.finish("本群还没有人猜对歌曲哦，快来玩“猜歌”吧！")

    try:
        info = await bot.get_group_info(group_id=event.group_id)
        group_name = info.get("group_name") or f"群 {event.group_id}"
    except ActionFailed as e:
        logger.warning(f"获取群 {event.group_id} 信息失败，排行榜改用群号: {e}")
        group_name = f"群 {event.group_id}"

    try:
        image = await render.leaderboard_image(group_name, rows)
    except Exception:
        logger.exception("猜歌排行榜渲染失败")
        lines = [
            f"No.{i} {name} - {score} 分" for i, (name, score) in enumerate(rows, 1)
        ]
        await matcher.finish(
            f"...排行榜图片生成失败，即将发送文本版：\n--- {group_name} 猜歌排行 ---\n"
            + "\n".join(lines)
        )
    await matcher.finish(image_segment(image))


resource_cmd = _command("猜歌资源", GAME_RULE)


@resource_cmd.handle()
async def handle_resource(matcher: Matcher):
    result = load_json(VERSION_FILE, default={})
    if not result.success:
        logger.warning(f"读取 masterdata 版本失败: {result.error}")
    data_version = result.data.get("dataVersion", "未知")

    try:
        image = await render.resource_image(songs.stats(), data_version)
    except Exception:
        logger.exception("猜歌资源图渲染失败")
        await matcher.finish(f"生成图片失败，请检查日志。\nDataVersion: {data_version}")
    await matcher.finish(image_segment(image))
