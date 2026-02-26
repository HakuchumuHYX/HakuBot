# pjsk_guess_song/handlers/listen.py
"""
存放所有“听”指令
"""
import time
from pathlib import Path
from typing import Optional
from nonebot import on_command
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment, Bot

from .. import db_service, cache_service, game_service
from ..game_data import game_session_locks, active_game_sessions, last_game_end_time
from ..utils import (
    get_session_id, get_user_id, get_user_name,
    _is_group_allowed, _get_setting_for_group
)
from ...plugin_manager.enable import *
from ...utils.common import create_exact_command_rule
from ...utils.image_utils import path_to_base64_image, path_to_base64_record


async def _handle_listen_command(matcher: Matcher, bot: Bot, event: MessageEvent, mode: str,
                                 search_term: Optional[str]):
    user_id = str(event.user_id)
    # 检查听歌子功能是否启用
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("pjsk_guess_song", "listen", str(event.group_id), user_id):
            await matcher.finish("听歌功能在此群无法使用！")
            return
    """
    (重构) 统一处理所有"听歌"类指令的通用逻辑。
    """
    if not await _is_group_allowed(event): return

    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    async with lock:
        cooldown = _get_setting_for_group(event, "game_cooldown_seconds", 30)
        if time.time() - last_game_end_time.get(session_id, 0) < cooldown:
            remaining_time = cooldown - (time.time() - last_game_end_time.get(session_id, 0))
            time_display = f"{remaining_time:.3f}" if remaining_time < 1 else str(int(remaining_time))
            await matcher.finish(f"嗯......休息 {time_display} 秒再玩吧......")
        if session_id in active_game_sessions:
            await matcher.finish("......有一个正在进行的游戏或播放任务了呢。")

        user_id = get_user_id(event)
        listen_limit = _get_setting_for_group(event, "daily_listen_limit", 10)
        can_listen = await db_service.can_listen_song(user_id, listen_limit)
        if not can_listen:
            await matcher.finish(f"......你今天听歌的次数已达上限（{listen_limit}次），请明天再来吧......")

        # [重构]
        config = game_service.listen_modes[mode]
        if not getattr(cache_service, config['list_attr']):
            await matcher.finish(config['not_found_msg'])

        active_game_sessions[session_id] = {"placeholder": True, "type": "listen"}

    await matcher.send("正在加载数据……")

    try:
        # [重构]
        config = game_service.listen_modes[mode]
        song_to_play, mp3_source = await game_service.get_listen_song_and_path(mode, search_term)

        if not song_to_play or not mp3_source:
            no_match_msg = config['no_match_msg'].format(
                search_term=search_term) if search_term else "......出错了，找不到有效的音频文件。"
            await matcher.finish(no_match_msg)
            return

        jacket_source = cache_service.get_resource_path_or_url(
            f"music_jacket/{song_to_play['jacketAssetbundleName']}.png")
        msg_chain = Message(f"歌曲:{song_to_play['id']}. {song_to_play['title']} {config['title_suffix']}\n")
        if jacket_source:
            if isinstance(jacket_source, Path):
                msg_chain.append(path_to_base64_image(jacket_source))
            else:
                msg_chain.append(MessageSegment.image(file=jacket_source))

        await matcher.send(msg_chain)

        if isinstance(mp3_source, Path):
            await matcher.send(path_to_base64_record(mp3_source))
        else:
            await matcher.send(MessageSegment.record(file=mp3_source))

        user_id = get_user_id(event)
        await db_service.record_listen_song(user_id, get_user_name(event))

    except Exception as e:
        logger.error(f"处理听歌功能(模式: {mode})时出错: {e}", exc_info=True)
        await matcher.send("......播放时出错了，请联系管理员。")
    finally:
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()


# --- 动态注册所有听歌指令 ---
listen_commands = {
    "听钢琴": "piano",
    "听伴奏": "accompaniment",
    "听人声": "vocals",
    "听贝斯": "bass",
    "听鼓组": "drums"
}

for cmd, mode in listen_commands.items():
    def create_handler(current_mode: str):
        async def handler(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
            search_term = args.extract_plain_text().strip() or None
            await _handle_listen_command(matcher, bot, event, current_mode, search_term)

        return handler


    on_command(cmd, priority=10, block=True, rule=create_exact_command_rule(cmd)).handle()(create_handler(mode))

# --- [修改] 听普通 (独立处理器) ---
listen_normal = on_command("听",
                           priority=10,
                           block=True,
                           rule=create_exact_command_rule("听")
                           )


@listen_normal.handle()
async def _(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = str(event.user_id)
    # 检查听歌子功能是否启用
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("pjsk_guess_song", "listen", str(event.group_id), user_id):
            await matcher.finish("听歌功能在此群无法使用！")
            return

    if not await _is_group_allowed(event): return

    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    # --- [修改] 提前检查参数 ---
    content = args.extract_plain_text().strip()
    if not content:
        await matcher.finish("......请指定要听的歌曲名称或ID。例如：听 <歌名> [sekai/vs]")
        return
    # --- [修改] 结束 ---

    async with lock:
        cooldown = _get_setting_for_group(event, "game_cooldown_seconds", 30)
        if time.time() - last_game_end_time.get(session_id, 0) < cooldown:
            remaining_time = cooldown - (time.time() - last_game_end_time.get(session_id, 0))
            time_display = f"{remaining_time:.3f}" if remaining_time < 1 else str(int(remaining_time))
            await matcher.finish(f"嗯......休息 {time_display} 秒再玩吧......")
        if session_id in active_game_sessions:
            await matcher.finish("......有一个正在进行的游戏或播放任务了呢。")

        user_id = get_user_id(event)
        listen_limit = _get_setting_for_group(event, "daily_listen_limit", 10)
        can_listen = await db_service.can_listen_song(user_id, listen_limit)
        if not can_listen:
            await matcher.finish(f"......你今天听歌的次数已达上限（{listen_limit}次），请明天再来吧......")

        active_game_sessions[session_id] = {"placeholder": True, "type": "listen"}

    await matcher.send("正在加载数据……")

    try:
        # content 已在锁外获取

        song_query: Optional[str] = ""
        version_type = "sekai"  # 默认听 sekai ver

        parts = content.rsplit(maxsplit=1)

        # content 已确认不为空
        if len(parts) == 1:
            song_query = content
        else:  # len(parts) == 2
            query_part = parts[0]
            version_part = parts[1].lower()

            vs_aliases = ["vs", "v", "vocal", "vocal ver", "vs ver"]
            sekai_aliases = ["s", "sekai", "sekai ver"]

            if version_part in vs_aliases:
                version_type = "virtual_singer"
                song_query = query_part
            elif version_part in sekai_aliases:
                version_type = "sekai"
                song_query = query_part
            else:
                # 用户输入了 "歌名A 歌名B"，但 "歌名B" 不是版本指令，
                # 将整体视为歌名
                song_query = content

        # [重构] 调用新的 service 方法
        song_to_play, mp3_source, vocal_info = await game_service.get_normal_song_and_path(song_query, version_type)

        if not song_to_play:
            if song_query:
                await matcher.finish(f"......没有找到与 '{song_query}' 匹配的歌曲。")
            else:
                await matcher.finish("......没有找到任何歌曲。")
            return

        if not mp3_source or not vocal_info:
            await matcher.finish(f"......歌曲 \"{song_to_play['title']}\" 没有找到符合要求的音频文件。")
            return

        jacket_source = cache_service.get_resource_path_or_url(
            f"music_jacket/{song_to_play['jacketAssetbundleName']}.png")

        # 根据实际找到的版本生成标题后缀
        version_name = ""
        if vocal_info.get('musicVocalType') == 'virtual_singer':
            version_name = "(Virtual Singer Ver.)"
        elif vocal_info.get('musicVocalType') == 'sekai':
            version_name = "(Sekai Ver.)"
        elif vocal_info.get('musicVocalType') == 'another_vocal':
            version_name = "(Another Vocal)"  # 备用，正常不会到这里

        msg_chain = Message(f"歌曲:{song_to_play['id']}. {song_to_play['title']} {version_name}\n")
        if jacket_source:
            if isinstance(jacket_source, Path):
                msg_chain.append(path_to_base64_image(jacket_source))
            else:
                msg_chain.append(MessageSegment.image(file=jacket_source))

        await matcher.send(msg_chain)

        if isinstance(mp3_source, Path):
            await matcher.send(path_to_base64_record(mp3_source))
        else:
            await matcher.send(MessageSegment.record(file=mp3_source))

        user_id = get_user_id(event)
        await db_service.record_listen_song(user_id, get_user_name(event))

    except Exception as e:
        logger.error(f"处理 听歌 功能时出错: {e}", exc_info=True)
        await matcher.send("......播放时出错了，请联系管理员。")
    finally:
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()


# --- [修改] 结束 ---


# --- 听anvo 指令 ---
listen_anvo = on_command("听anvo",
                         aliases={"listen_anvo", "listen_anov", "听anov", "anvo", "anov"},
                         priority=10,
                         block=True,
                         rule=create_exact_command_rule("听anvo",
                                                        {"listen_anvo", "listen_anov", "听anov", "anvo", "anov"})
                         )


@listen_anvo.handle()
async def _(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = str(event.user_id)
    # 检查听歌子功能是否启用
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("pjsk_guess_song", "listen", str(event.group_id), user_id):
            await matcher.finish("听歌功能在此群无法使用！")
            return

    if not await _is_group_allowed(event): return

    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    async with lock:
        cooldown = _get_setting_for_group(event, "game_cooldown_seconds", 30)
        if time.time() - last_game_end_time.get(session_id, 0) < cooldown:
            remaining_time = cooldown - (time.time() - last_game_end_time.get(session_id, 0))
            time_display = f"{remaining_time:.3f}" if remaining_time < 1 else str(int(remaining_time))
            await matcher.finish(f"嗯......休息 {time_display} 秒再玩吧......")
        if session_id in active_game_sessions:
            await matcher.finish("......有一个正在进行的游戏或播放任务了呢。")

        user_id = get_user_id(event)
        listen_limit = _get_setting_for_group(event, "daily_listen_limit", 10)
        can_listen = await db_service.can_listen_song(user_id, listen_limit)
        if not can_listen:
            await matcher.finish(f"......你今天听歌的次数已达上限（{listen_limit}次），请明天再来吧......")

        if not cache_service.another_vocal_songs:
            await matcher.finish("......抱歉，没有找到任何可用的 Another Vocal 歌曲。")
            return

        active_game_sessions[session_id] = {"placeholder": True, "type": "listen"}

    await matcher.send("正在加载数据……")

    try:
        content = args.extract_plain_text().strip()

        # [重构]
        song_to_play, vocal_info = await game_service.get_anvo_song_and_vocal(content)

        if not song_to_play:
            if content:
                await matcher.finish(f"......没有找到与 '{content}' 匹配的歌曲或角色。")
            else:
                await matcher.finish("......内部错误，请联系管理员。")
            return

        if vocal_info is None:
            await matcher.finish(f"......歌曲 \"{song_to_play['title']}\" 没有找到符合要求的 Another Vocal 版本。")
            return

        if vocal_info == 'list_versions':
            anov_list = [v for v in song_to_play.get('vocals', []) if v.get('musicVocalType') == 'another_vocal']
            if not anov_list:
                await matcher.finish(f"......歌曲 '{song_to_play['title']}' 没有 Another Vocal 版本。")
                return

            reply = f"歌曲 \"{song_to_play['title']}\" 有以下 Another Vocal 版本:\n"
            lines = []
            for v in anov_list:
                names = [cache_service.character_data.get(str(c['characterId']), {}).get('fullName', '未知') for c in
                         v.get('characters', [])]
                abbrs = [cache_service.character_data.get(str(c['characterId']), {}).get('name', 'unk') for c in
                         v.get('characters', [])]
                lines.append(f"  - {' + '.join(names)} ({'+'.join(abbrs)})")
            reply += "\n".join(lines)
            reply += f"\n\n请使用 /听anvo {song_to_play['id']} <角色> 来播放。"
            await matcher.finish(reply)
            return

        # [重构]
        mp3_source_path = await game_service.audio_processor.process_anvo_audio(song_to_play, vocal_info)

        if not mp3_source_path:
            await matcher.finish("......处理音频时出错了（FFmpeg）。")
            return

        mp3_source = Path(mp3_source_path)
        jacket_source = cache_service.get_resource_path_or_url(
            f"music_jacket/{song_to_play['jacketAssetbundleName']}.png")
        char_ids = [c.get('characterId') for c in vocal_info.get('characters', [])]
        char_names = [cache_service.character_data.get(str(cid), {}).get('fullName', '未知') for cid in char_ids]

        msg_chain = Message(
            f"歌曲:{song_to_play['id']}. {song_to_play['title']} (Another Vocal - {' + '.join(char_names)})\n")
        if jacket_source:
            if isinstance(jacket_source, Path):
                msg_chain.append(path_to_base64_image(jacket_source))
            else:
                msg_chain.append(MessageSegment.image(file=jacket_source))

        await matcher.send(msg_chain)
        await matcher.send(path_to_base64_record(mp3_source))

        user_id = get_user_id(event)
        await db_service.record_listen_song(user_id, get_user_name(event))

    except Exception as e:
        logger.error(f"处理听anvo功能时出错: {e}", exc_info=True)
        await matcher.send("......播放时出错了，请联系管理员。")
    finally:
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()
