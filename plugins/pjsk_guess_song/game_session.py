# pjsk_guess_song/game_session.py
"""
存放核心的游戏会话管理逻辑和答案处理器
"""
import asyncio
import time
from pathlib import Path
from typing import Dict
from nonebot import on_message, get_bot
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment, Bot, GroupMessageEvent

# 导入服务和配置
from . import plugin_config, db_service
# 导入全局状态
from .game_data import active_game_sessions, last_game_end_time
# 导入辅助函数
from .utils import get_session_id, get_user_id, get_user_name, _get_setting_for_group


async def _end_game_session(session_id: str, reason_msg: str):
    if session_id not in active_game_sessions:
        return

    game_data = active_game_sessions.pop(session_id, None)
    if not game_data or game_data.get("type") == "listen":
        return

    try:
        current_task = asyncio.current_task()

        main_timeout_task = game_data.get('timeout_task')
        if main_timeout_task and not main_timeout_task.done() and main_timeout_task is not current_task:
            main_timeout_task.cancel()
            logger.debug(f"已取消游戏 {session_id} 的主要超时任务。")

        bonus_timeout_task = game_data.get('bonus_task')
        if bonus_timeout_task and not bonus_timeout_task.done() and bonus_timeout_task is not current_task:
            bonus_timeout_task.cancel()
            logger.debug(f"已取消游戏 {session_id} 的奖励时间任务。")
    except Exception as e:
        logger.error(f"取消游戏 {session_id} 的任务时出错: {e}")

    last_game_end_time[session_id] = time.time()

    correct_players = game_data.get('correct_players', {})

    try:
        score_to_add = game_data.get('score', 1)
        start_event = game_data.get('start_event')

        # 仅在群聊中且有玩家答对时记录分数
        if isinstance(start_event, GroupMessageEvent) and correct_players:
            group_id = str(start_event.group_id)
            score_tasks = []
            for user_id, player_info in correct_players.items():
                user_name = player_info.get('name', user_id)
                score_tasks.append(
                    db_service.add_score(user_id, group_id, score_to_add, user_name)
                )

            if score_tasks:
                await asyncio.gather(*score_tasks)
                logger.info(f"已为群 {group_id} 的 {len(score_tasks)} 名玩家记录 {score_to_add} 分。")
    except Exception as e:
        logger.error(f"记录分数时出错: {e}", exc_info=True)

    if correct_players:
        winner_names = "、".join(player['name'] for player in correct_players.values())
        summary_text = f"{reason_msg}\n本轮答对的玩家有：\n{winner_names}"
    else:
        summary_text = f"{reason_msg} 好像......没有人答对......"

    try:
        # 从 game_data 中恢复 bot 和 event
        bot = get_bot(game_data['bot_id'])
        event = game_data['start_event']

        await bot.send(event, summary_text)
        # 发送答案
        await bot.send(event, game_data['answer_reveal_messages'])
    except Exception as e:
        logger.error(f"发送游戏结果失败: {e}")


async def _game_timeout_task(session_id: str, timeout: int):
    """(新的) 游戏超时任务"""
    await asyncio.sleep(timeout)

    # 检查游戏是否还存在 (可能已因答对或次数满而结束)
    if session_id in active_game_sessions:
        game_data = active_game_sessions.get(session_id, {})
        # 确保是游戏会话，而不是听歌会话
        if game_data.get("type") == "listen":
            return

        # --- [修改] ---
        # 移除 "max_attempts" 和 "guess_count" 检查
        # 游戏超时任务现在只负责 "时间到"
        reason_msg = "时间到！"
        # --- [修改] 结束 ---

        logger.info(f"游戏 {session_id} 超时结束。")
        await _end_game_session(session_id, reason_msg)


async def _run_game_session(
        bot: Bot,
        event: MessageEvent,
        game_data: Dict,
        intro_messages: Message,
        answer_reveal_messages: Message
):
    session_id = get_session_id(event)
    debug_mode = plugin_config.debug_mode
    timeout_seconds = _get_setting_for_group(event, "answer_timeout", 30)

    try:
        # 1. 发送音频和介绍
        clip_path = Path(game_data["clip_path"])
        await bot.send(event, MessageSegment.record(file=clip_path.absolute().as_uri()))
        await bot.send(event, intro_messages)

        if debug_mode:
            logger.info("[猜歌插件] 调试模式已启用，立即显示答案")
            await bot.send(event, answer_reveal_messages)
            last_game_end_time[session_id] = time.time()
            if session_id in active_game_sessions:
                active_game_sessions.pop(session_id)  # 确保清理
            return

        # 2. (核心) 设置全局游戏状态
        game_data['answer_reveal_messages'] = answer_reveal_messages
        game_data['correct_players'] = {}
        game_data['first_correct_answer_time'] = 0
        game_data['guessed_users'] = set()  # (这个字段似乎未被使用，但保留它)

        # --- [修改] ---
        # game_data['guess_attempts_count'] = 0 # (移除全局计数器)
        game_data['user_guess_counts'] = {}  # (改为按用户计次的字典)
        # --- [修改] 结束 ---

        game_data['start_event'] = event  # 存储初始 event 用于后续发送消息
        game_data['bot_id'] = bot.self_id  # 存储 bot self_id
        game_data['type'] = 'game'  # 标记为游戏会话

        active_game_sessions[session_id] = game_data

        timeout_task = asyncio.create_task(_game_timeout_task(session_id, timeout_seconds))
        active_game_sessions[session_id]['timeout_task'] = timeout_task

    except Exception as e:
        logger.error(f"发送消息失败: {e}. 游戏中断。", exc_info=True)
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()


answer_handler = on_message(priority=5, block=False)


@answer_handler.handle()
async def handle_game_answer(bot: Bot, event: MessageEvent, state: T_State, matcher: Matcher):
    session_id = get_session_id(event)
    answer_text = event.get_plaintext().strip()

    if not answer_text.isdigit():
        return

    matcher.stop_propagation()

    game_data = active_game_sessions.get(session_id)
    if not game_data or game_data.get("type") != "game":
        return  # 是数字，但没有游戏，忽略

    user_id = get_user_id(event)
    user_name = get_user_name(event)

    # --- [核心修改] ---
    max_guess_attempts = _get_setting_for_group(event, "max_guess_attempts", 10)

    # 1. 获取该用户的个人猜测次数
    user_count = game_data.get('user_guess_counts', {}).get(user_id, 0)

    # 2. 检查该用户的个人次数是否已达上限
    if max_guess_attempts > 0 and user_count >= max_guess_attempts:
        # 如果已达上限，回复该用户并停止处理
        await matcher.send("您的猜歌次数已用完", at_sender=True)
        return

    # 3. 为该用户增加一次猜测次数
    game_data['user_guess_counts'][user_id] = user_count + 1
    # (原有的全局计数器 `game_data['guess_attempts_count'] += 1` 已删除)
    # --- [核心修改] 结束 ---

    is_correct = False
    try:
        answer_num = int(answer_text)
        if 1 <= answer_num <= game_data.get("num_options", 12):
            if answer_num == game_data['correct_answer_num']:
                is_correct = True
    except ValueError:
        pass

    if is_correct:
        # 答对了，且在该用户的次数限制内
        if user_id not in game_data['correct_players']:
            game_data['correct_players'][user_id] = {'name': user_name}
            is_first_correct_answer = (game_data['first_correct_answer_time'] == 0)
            if is_first_correct_answer:
                game_data['first_correct_answer_time'] = time.time()
                end_game_early = _get_setting_for_group(event, "end_game_after_bonus_time", True)
                bonus_time = _get_setting_for_group(event, "bonus_time_after_first_answer", 5)

                if end_game_early and bonus_time > 0:
                    async def _bonus_time_end_task(sid, delay):
                        await asyncio.sleep(delay)
                        if sid in active_game_sessions:
                            logger.info(f"游戏 {sid} 奖励时间到，提前结束。")
                            await _end_game_session(sid, "奖励时间到！")

                    bonus_task = asyncio.create_task(_bonus_time_end_task(session_id, bonus_time))
                    game_data['bonus_task'] = bonus_task

    # --- [修改] ---
    # 移除 "全局猜测次数达到上限时结束游戏" 的逻辑
    # if max_guess_attempts > 0 and game_data['guess_attempts_count'] >= max_guess_attempts:
    #     ... (逻辑已删除)
    # --- [修改] 结束 ---