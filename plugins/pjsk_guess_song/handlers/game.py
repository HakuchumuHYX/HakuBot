# pjsk_guess_song/handlers/game.py
"""
存放所有开始游戏的指令
"""
import re
import random
import time
from pathlib import Path
from nonebot import on_command
from nonebot.log import logger
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment, Bot, GroupMessageEvent

from .. import db_service, cache_service, plugin_config, game_service, image_service
from ..game_data import game_session_locks, active_game_sessions, last_game_end_time
from ..utils import (
    get_session_id, get_user_id, get_user_name,
    _check_game_start_conditions, _get_setting_for_group
)
from ..game_session import _run_game_session
from ...plugin_manager.enable import is_plugin_enabled
from ...utils.common import create_exact_command_rule

# --- 猜歌指令 ---
start_guess_song_unified = on_command(
    "猜歌",
    aliases={
        "gs",
        "猜歌1", "猜歌2", "猜歌3", "猜歌4", "猜歌5", "猜歌6", "猜歌7",
        "gs1", "gs2", "gs3", "gs4", "gs5", "gs6", "gs7"
    },
    priority=10,
    block=True,
    rule=create_exact_command_rule("猜歌", {"gs", "猜歌1", "猜歌2", "猜歌3", "猜歌4", "猜歌5", "猜歌6", "猜歌7", "gs1", "gs2", "gs3", "gs4", "gs5", "gs6", "gs7"})
)


@start_guess_song_unified.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await start_guess_song_unified.finish("猜歌功能在此群无法使用！")
            return

    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    cmd = event.get_plaintext()
    match_cmd = re.match(r"^(猜歌|gs)(\d)?", cmd)
    mode_key = 'normal'
    if match_cmd and match_cmd.group(2):
        mode_key = match_cmd.group(2)

    if plugin_config.lightweight_mode and mode_key in ['1', '2']:
        # [重构]
        original_mode_name = game_service.game_modes[mode_key]['name']
        await start_guess_song_unified.finish(f'......轻量模式已启用，模式"{original_mode_name}"已自动切换为普通模式。')

    async with lock:
        can_start, message = await _check_game_start_conditions(event)
        if not can_start:
            if message:
                await start_guess_song_unified.finish(message)
            return
        active_game_sessions[session_id] = {"placeholder": True, "type": "game_init"}

    await start_guess_song_unified.send("正在加载数据……")

    try:
        initiator_id = get_user_id(event)
        initiator_name = get_user_name(event)
        is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)
        await db_service.consume_daily_play_attempt(initiator_id, initiator_name, session_id, is_independent_limit)

        # [重构]
        mode_config = game_service.game_modes.get(mode_key)
        if not mode_config:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_guess_song_unified.finish(f"......未知的猜歌模式 '{mode_key}'。")
            return

        game_kwargs = mode_config['kwargs'].copy()
        game_kwargs['score'] = mode_config.get('score', 1)

        if 'play_preprocessed' in game_kwargs:
            game_type_suffix = game_kwargs['play_preprocessed']
        elif 'melody_to_piano' in game_kwargs:
            game_type_suffix = 'piano'
        elif 'reverse_audio' in game_kwargs:
            game_type_suffix = 'reverse'
        elif 'speed_multiplier' in game_kwargs:
            game_type_suffix = 'speed_2x'
        else:
            game_type_suffix = 'normal'
        game_kwargs['game_type'] = f"guess_song_{game_type_suffix}"

        # [重构]
        game_data = await game_service.get_game_clip(**game_kwargs)
        if not game_data:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_guess_song_unified.finish("......开始游戏失败，可能是缺少资源文件或配置错误。")
            return

        correct_song = game_data['song']
        if not cache_service.song_data:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_guess_song_unified.finish("......歌曲数据未加载，无法生成选项。")
            return

        other_songs = random.sample([s for s in cache_service.song_data if s['id'] != correct_song['id']], 11)
        options = [correct_song] + other_songs
        random.shuffle(options)

        game_data['options'] = options
        game_data['correct_answer_num'] = options.index(correct_song) + 1
        game_data['num_options'] = 12

        logger.info(f"[猜歌插件] 新游戏开始. 答案: {correct_song['title']} (选项 {game_data['correct_answer_num']})")

        # [重构]
        options_img_path = await image_service.create_options_image(options)

        answer_timeout = _get_setting_for_group(event, "answer_timeout", 30)
        intro_text = f".......嗯\n这首歌是？请在{answer_timeout}秒内发送编号回答。\n"

        intro_messages = Message(intro_text)
        if options_img_path:
            img_path = Path(options_img_path)
            intro_messages.append(MessageSegment.image(file=img_path.absolute().as_uri()))

        jacket_source = cache_service.get_resource_path_or_url(
            f"music_jacket/{correct_song['jacketAssetbundleName']}.png")
        answer_reveal_messages = Message(f"正确答案是: {game_data['correct_answer_num']}. {correct_song['title']}\n")
        if jacket_source:
            if isinstance(jacket_source, Path):
                answer_reveal_messages.append(MessageSegment.image(file=jacket_source.absolute().as_uri()))
            else:
                answer_reveal_messages.append(MessageSegment.image(file=jacket_source))

        await _run_game_session(bot, event, game_data, intro_messages, answer_reveal_messages)

    except Exception as e:
        logger.error(f"游戏启动过程中发生未处理的异常: {e}", exc_info=True)
        await start_guess_song_unified.send("......开始游戏时发生内部错误，已中断。")
        if session_id in active_game_sessions: active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()


# --- 随机猜歌 ---
start_random_guess_song = on_command("随机猜歌",
                                     aliases={"rgs"},
                                     priority=10,
                                     block=True,
                                     rule=create_exact_command_rule("随机猜歌", {"rgs"})
                                     )


@start_random_guess_song.handle()
async def _(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await start_random_guess_song.finish("随机猜歌功能在此群无法使用！")
            return

    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    async with lock:
        can_start, message = await _check_game_start_conditions(event)
        if not can_start:
            if message:
                await start_random_guess_song.finish(message)
            return
        active_game_sessions[session_id] = {"placeholder": True, "type": "game_init"}

    await start_random_guess_song.send("正在加载数据……")

    try:
        initiator_id = get_user_id(event)
        initiator_name = get_user_name(event)
        is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)
        await db_service.consume_daily_play_attempt(initiator_id, initiator_name, session_id, is_independent_limit)

        # [重构]
        combined_kwargs, total_score, effect_names_display, mode_name_str = game_service.get_random_mode_config()
        if not combined_kwargs:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_random_guess_song.finish("......随机模式启动失败，没有可用的效果组合。请检查资源文件。")
            return

        await start_random_guess_song.send(f"......本轮应用效果：【{effect_names_display}】(总计{total_score}分)")

        combined_kwargs['random_mode_name'] = f"random_{mode_name_str}"
        combined_kwargs['score'] = total_score
        combined_kwargs['game_type'] = 'guess_song_random'

        # [重构]
        game_data = await game_service.get_game_clip(**combined_kwargs)
        if not game_data:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_random_guess_song.finish("......开始游戏失败，可能是缺少资源文件或配置错误。")
            return

        correct_song = game_data['song']
        if not cache_service.song_data:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_guess_song_unified.finish("......歌曲数据未加载，无法生成选项。")
            return

        other_songs = random.sample([s for s in cache_service.song_data if s['id'] != correct_song['id']], 11)
        options = [correct_song] + other_songs
        random.shuffle(options)

        game_data['options'] = options
        game_data['correct_answer_num'] = options.index(correct_song) + 1
        game_data['num_options'] = 12

        logger.info(f"[猜歌插件] 新游戏开始. 答案: {correct_song['title']} (选项 {game_data['correct_answer_num']})")

        # [重构]
        options_img_path = await image_service.create_options_image(options)
        timeout_seconds = _get_setting_for_group(event, "answer_timeout", 30)
        intro_text = f".......嗯\n这首歌是？请在{timeout_seconds}秒内发送编号回答。\n"

        intro_messages = Message(intro_text)
        if options_img_path:
            img_path = Path(options_img_path)
            intro_messages.append(MessageSegment.image(file=img_path.absolute().as_uri()))

        jacket_source = cache_service.get_resource_path_or_url(
            f"music_jacket/{correct_song['jacketAssetbundleName']}.png")
        answer_reveal_messages = Message(f"正确答案是: {game_data['correct_answer_num']}. {correct_song['title']}\n")
        if jacket_source:
            if isinstance(jacket_source, Path):
                answer_reveal_messages.append(MessageSegment.image(file=jacket_source.absolute().as_uri()))
            else:
                answer_reveal_messages.append(MessageSegment.image(file=jacket_source))

        await _run_game_session(bot, event, game_data, intro_messages, answer_reveal_messages)

    except Exception as e:
        logger.error(f"随机游戏启动过程中发生未处理的异常: {e}", exc_info=True)
        await start_random_guess_song.send("......开始游戏时发生内部错误，已中断。")
    finally:
        if session_id in active_game_sessions and active_game_sessions[session_id].get("type") == "game_init":
            active_game_sessions.pop(session_id)
            last_game_end_time[session_id] = time.time()


# --- 猜歌手 ---
start_vocalist_game = on_command("猜歌手",
                                 priority=10,
                                 block=True,
                                 rule=create_exact_command_rule("猜歌手")
                                 )


@start_vocalist_game.handle()
async def _(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await start_vocalist_game.finish("猜歌手功能在此群无法使用！")
            return

    if not cache_service.another_vocal_songs:
        await start_vocalist_game.finish("......抱歉，没有找到包含 another_vocal 的歌曲，无法开始游戏。")
        return

    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    async with lock:
        can_start, message = await _check_game_start_conditions(event)
        if not can_start:
            if message:
                await start_vocalist_game.finish(message)
            return
        active_game_sessions[session_id] = {"placeholder": True, "type": "game_init"}

    await start_vocalist_game.send("正在加载数据……")

    try:
        initiator_id = get_user_id(event)
        initiator_name = get_user_name(event)
        is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)
        await db_service.consume_daily_play_attempt(initiator_id, initiator_name, session_id, is_independent_limit)

        song = random.choice(cache_service.another_vocal_songs)
        all_vocals = song.get('vocals', [])
        another_vocals = [v for v in all_vocals if v.get('musicVocalType') == 'another_vocal']

        if not another_vocals:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_vocalist_game.finish("......没有找到合适的歌曲版本，游戏无法开始。")
            return

        correct_vocal_version = random.choice(another_vocals)

        # [重构]
        game_data = await game_service.get_game_clip(
            force_song_object=song,
            force_vocal_version=correct_vocal_version,
            speed_multiplier=1.5,
            game_type='guess_song_vocalist',
            guess_type='vocalist',
            mode_name='猜歌手'
        )
        if not game_data:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_vocalist_game.finish("......准备音频失败，游戏无法开始。")
            return

        random.shuffle(another_vocals)
        game_data['num_options'] = len(another_vocals)
        game_data['correct_answer_num'] = another_vocals.index(correct_vocal_version) + 1
        game_data['game_mode'] = 'vocalist'

        def get_vocalist_name(vocal_info):
            char_list = vocal_info.get('characters', [])
            if not char_list: return "未知"
            char_names = []
            for char in char_list:
                char_id = char.get('characterId')
                char_data = cache_service.character_data.get(str(char_id))
                if char_data:
                    char_names.append(char_data.get("fullName", char_data.get("name", "未知")))
                else:
                    char_names.append("未知")
            return ' + '.join(char_names)

        compact_options_text = ""
        for i, vocal in enumerate(another_vocals):
            vocalist_name = get_vocalist_name(vocal)
            compact_options_text += f"{i + 1}. {vocalist_name}\n"

        timeout_seconds = _get_setting_for_group(event, "answer_timeout", 30)
        intro_text = f"这首歌是【{song['title']}】，正在演唱的是谁？[1.5倍速]\n请在{timeout_seconds}秒内发送编号回答。\n\n⚠️ 测试功能\n\n{compact_options_text}"
        jacket_source = cache_service.get_resource_path_or_url(f"music_jacket/{song['jacketAssetbundleName']}.png")

        intro_messages = Message(intro_text)
        if jacket_source:
            if isinstance(jacket_source, Path):
                intro_messages.append(MessageSegment.image(file=jacket_source.absolute().as_uri()))
            else:
                intro_messages.append(MessageSegment.image(file=jacket_source))

        correct_vocalist_name = get_vocalist_name(correct_vocal_version)
        answer_reveal_messages = Message(f"正确答案是: {game_data['correct_answer_num']}. {correct_vocalist_name}")

        await _run_game_session(bot, event, game_data, intro_messages, answer_reveal_messages)

    except Exception as e:
        logger.error(f"猜歌手游戏启动过程中发生未处理的异常: {e}", exc_info=True)
        await start_vocalist_game.send("......开始游戏时发生内部错误，已中断。")
    finally:
        if session_id in active_game_sessions and active_game_sessions[session_id].get("type") == "game_init":
            active_game_sessions.pop(session_id)
            last_game_end_time[session_id] = time.time()