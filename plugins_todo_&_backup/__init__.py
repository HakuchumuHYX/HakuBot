# pjsk_guess_song/__init__.py

import asyncio
import json
import random
import time
import os
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime, timedelta
from collections import defaultdict
from nonebot.matcher import Matcher
import aiohttp
from nonebot import on_command, on_message, get_driver, get_bot
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import (
    Message,
    MessageEvent,
    GroupMessageEvent,
    MessageSegment,
    Bot,
)
from nonebot.rule import Rule
from nonebot.typing import T_State

# 导入服务
from .services.db_service import DBService
from .services.audio_service import AudioService
from .services.cache_service import CacheService
from .config import plugin_config, data_dir, CONFIG_FILE_PATH

# --- 插件元数据 ---
__plugin_meta__ = PluginMetadata(
    name="pjsk_guess_song",
    description="PJSK猜歌插件",
    usage="""
    🎵 基础指令
      `猜歌` - 普通
      `猜歌 1-7` - 对应特殊模式
    🎲 高级指令
      `随机猜歌` - 随机组合效果
      `猜歌手` - 竞猜演唱者
      `听<模式> [歌名/ID]` - 播放特殊音轨 (模式: 钢琴, 伴奏, 人声, 贝斯, 鼓组)
      `听anvo [歌名/ID] [角色名缩写]` - 播放指定或随机的 Another Vocal
    📊 其他功能
      `猜歌帮助` - 显示此帮助信息
    """,
    type="application",
    homepage="https://github.com/nichinichisou0609/astrbot_plugin_pjsk_guess_song",
    config=plugin_config.__class__,
)

# --- 全局状态 ---
PLUGIN_VERSION = "1.1.3"
plugin_dir = Path(__file__).parent
resources_dir = plugin_dir / "resources"
output_dir = data_dir / "output"
# (data_dir 已从 config.py 导入)
output_dir.mkdir(parents=True, exist_ok=True)


# --- 初始化服务 ---
db_path = data_dir / "guess_song_data.db"
db_service = DBService(str(db_path))
cache_service = CacheService(resources_dir, output_dir, plugin_config)
audio_service = AudioService(cache_service, resources_dir, output_dir, plugin_config, PLUGIN_VERSION)

# --- 游戏状态管理 ---
# 用于替换 astrbot 的 context.game_session_locks
game_session_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
# 用于替换 astrbot 的 context.active_game_sessions
# 这是重构的核心：用一个字典存储所有活跃游戏的状态
active_game_sessions: Dict[str, Dict] = {}
last_game_end_time: Dict[str, float] = {}


# --- 辅助函数 ---

def get_session_id(event: MessageEvent) -> str:
    """为 nonebot event 生成一个唯一的会话 ID"""
    if isinstance(event, GroupMessageEvent):
        return f"onebot:group:{event.group_id}"
    else:
        # 私聊
        return f"onebot:private:{event.user_id}"


def get_user_id(event: MessageEvent) -> str:
    return str(event.user_id)


def get_user_name(event: MessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


async def _is_group_allowed(event: MessageEvent) -> bool:
    """检查群组是否在白名单中"""
    whitelist = plugin_config.group_whitelist
    if not whitelist:
        return True  # 白名单为空，允许所有

    if isinstance(event, GroupMessageEvent):
        is_in_whitelist = str(event.group_id) in whitelist
        if not is_in_whitelist:
            try:
                # 尝试发送消息，失败也无妨
                await get_bot().send(event, "本群未启用猜歌功能")
            except Exception as e:
                logger.error(f"发送非白名单群聊消息失败: {e}")
        return is_in_whitelist

    return True  # 私聊默认允许


def _get_setting_for_group(event: MessageEvent, key: str, default: any) -> any:
    """
    Nonebot 适配版设置获取。
    直接从加载的 plugin_config 对象中读取属性。
    """
    # 由于 Pydantic 模型使用 snake_case 键，
    # 且原版 main.py 内部调用也使用 snake_case (e.g., "daily_play_limit")
    # 我们可以直接 getattr
    return getattr(plugin_config, key, default)


async def _check_game_start_conditions(event: MessageEvent) -> Tuple[bool, Optional[str]]:
    """检查是否可以开始新游戏"""
    if not await _is_group_allowed(event):
        return False, None

    # --- 检查游戏是否在禁用时段 ---
    now_time = datetime.now().time()
    disable_periods = _get_setting_for_group(event, "disable_guess_song_periods", [])
    if isinstance(disable_periods, list):
        for period in disable_periods:
            try:
                start_time = datetime.strptime(period["start"], "%H:%M").time()
                end_time = datetime.strptime(period["end"], "%H:%M").time()
                if start_time <= now_time < end_time:
                    default_msg = f"当前时段 ({period['start']} - {period['end']}) 猜歌功能已禁用。"
                    return False, period.get("message", default_msg)
            except (KeyError, ValueError) as e:
                logger.warning(f"跳过格式错误的禁用时段配置: {period}, 错误: {e}")
                continue

    session_id = get_session_id(event)
    cooldown = _get_setting_for_group(event, "game_cooldown_seconds", 30)
    limit = _get_setting_for_group(event, "daily_play_limit", 15)
    debug_mode = plugin_config.debug_mode
    is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)

    if not debug_mode and time.time() - last_game_end_time.get(session_id, 0) < cooldown:
        remaining_time = cooldown - (time.time() - last_game_end_time.get(session_id, 0))
        time_display = f"{remaining_time:.3f}" if remaining_time < 1 else str(int(remaining_time))
        return False, f"嗯......休息 {time_display} 秒再玩吧......"

    if session_id in active_game_sessions:
        return False, "......有一个正在进行的游戏了呢。"

    can_play = await db_service.can_play(get_user_id(event), limit, session_id, is_independent_limit)
    if not debug_mode and not can_play:
        limit_type = "本群" if is_independent_limit else "你"
        return False, f"......{limit_type}今天的游戏次数已达上限（{limit}次），请明天再来吧......"

    return True, None


# --- Nonebot 启动/关闭 钩子 ---
driver = get_driver()


@driver.on_startup
async def _on_startup():
    """Nonebot 启动时执行异步初始化"""
    await db_service.init_db()
    await cache_service.load_resources_and_manifest()
    # 启动后台清理任务
    asyncio.create_task(cache_service.periodic_cleanup_task())
    logger.info("PJSK 猜歌插件服务已启动。")


@driver.on_shutdown
async def _on_shutdown():
    """Nonebot 关闭时执行清理"""
    await audio_service.terminate()
    await cache_service.terminate()
    logger.info("PJSK 猜歌插件服务已终止。")


# --- 游戏核心逻辑 (替换 session_waiter) ---

async def _end_game_session(session_id: str, reason_msg: str):
    """
    (新的) 统一的游戏结束处理函数
    """
    if session_id not in active_game_sessions:
        return

    game_data = active_game_sessions.pop(session_id, None)
    if not game_data or game_data.get("type") == "listen":
        return

    # --- [BUGFIX] 开始：取消所有活跃的异步任务 ---
    try:
        # [V2 修复] 获取当前正在执行 _end_game_session 的任务
        current_task = asyncio.current_task()

        main_timeout_task = game_data.get('timeout_task')
        # 确保任务存在、未完成，并且 *不是* 当前任务 (避免任务“自杀”)
        if main_timeout_task and not main_timeout_task.done() and main_timeout_task is not current_task:
            main_timeout_task.cancel()
            logger.debug(f"已取消游戏 {session_id} 的主要超时任务。")

        bonus_timeout_task = game_data.get('bonus_task')
        # 确保任务存在、未完成，并且 *不是* 当前任务 (避免任务“自杀”)
        if bonus_timeout_task and not bonus_timeout_task.done() and bonus_timeout_task is not current_task:
            bonus_timeout_task.cancel()
            logger.debug(f"已取消游戏 {session_id} 的奖励时间任务。")
    except Exception as e:
        logger.error(f"取消游戏 {session_id} 的任务时出错: {e}")
    # --- [BUGFIX] 结束 ---

    last_game_end_time[session_id] = time.time()

    correct_players = game_data.get('correct_players', {})

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

        start_event = game_data.get('start_event')
        max_attempts = 10
        if start_event:
            max_attempts = _get_setting_for_group(start_event, "max_guess_attempts", 10)

        guess_count = game_data.get('guess_attempts_count', 0)
        reason_msg = f"本轮猜测已达上限({max_attempts}次)！" if guess_count >= max_attempts else "时间到！"

        logger.info(f"游戏 {session_id} 超时结束。")
        await _end_game_session(session_id, reason_msg)


async def _run_game_session(
        bot: Bot,
        event: MessageEvent,
        game_data: Dict,
        intro_messages: Message,
        answer_reveal_messages: Message
):
    """
    (重构) 游戏会话执行器
    不再使用 session_waiter，而是设置全局状态
    """
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
        game_data['guessed_users'] = set()
        game_data['guess_attempts_count'] = 0
        game_data['start_event'] = event  # 存储初始 event 用于后续发送消息
        game_data['bot_id'] = bot.self_id  # 存储 bot self_id
        game_data['type'] = 'game'  # 标记为游戏会话

        active_game_sessions[session_id] = game_data

        # 3. 启动超时任务
        # asyncio.create_task(_game_timeout_task(session_id, timeout_seconds)) # [BUGFIX]

        # --- [BUGFIX] 替换为以下内容 ---
        timeout_task = asyncio.create_task(_game_timeout_task(session_id, timeout_seconds))
        active_game_sessions[session_id]['timeout_task'] = timeout_task
        # --- [BUGFIX] 结束 ---

    except Exception as e:
        logger.error(f"发送消息失败: {e}. 游戏中断。", exc_info=True)
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()


# --- (新的) 游戏答案处理器 ---

answer_handler = on_message(priority=5, block=False)


# pjsk_guess_song/__init__.py (替换从 382 行开始的函数)

@answer_handler.handle()
async def handle_game_answer(bot: Bot, event: MessageEvent, state: T_State, matcher: Matcher):
    """
    (V8) 最终修复版
    - P5, block=False (允许指令通过)
    - 检查是否为数字，如果 *是* 数字，则手动停止传播 (修复 V4 "连续发送" Bug)
    - 保持 V4 的核心逻辑 (修复 V1-V3 "次数" Bug)
    """
    session_id = get_session_id(event)
    answer_text = event.get_plaintext().strip()

    # [V8 关键修复]
    # 检查消息是不是数字
    if not answer_text.isdigit():
        # 不是数字 (是 "猜歌" 或 "hello")，P5 处理器什么都不做。
        # 因为 block=False，事件将自动流向 P10 指令处理器。
        return

    # --- 从这里开始，我们确定收到的是一个数字答案 ---

    # [V8 关键修复]
    # 它 *是* 一个数字，我们 *必须* 在此停止事件传播，
    # 否则它可能会被其他插件处理，或导致 V4 的重复发送 Bug。
    matcher.stop_propagation()

    # 1. 检查此会话是否有正在进行的游戏
    game_data = active_game_sessions.get(session_id)
    if not game_data or game_data.get("type") != "game":
        return  # 是数字，但没有游戏，忽略

    # 2. 提取用户信息
    user_id = get_user_id(event)
    user_name = get_user_name(event)

    # 3. 检查游戏是否 *已经* 因次数耗尽而结束
    max_guess_attempts = _get_setting_for_group(event, "max_guess_attempts", 10)
    if max_guess_attempts > 0 and game_data['guess_attempts_count'] >= max_guess_attempts:
        return  # 游戏已结束，不再处理

    # 4. [V4 核心逻辑] 消耗总次数
    game_data['guess_attempts_count'] += 1
    remaining_attempts = max_guess_attempts - game_data['guess_attempts_count']

    # 5. 检查答案是否正确
    is_correct = False
    try:
        answer_num = int(answer_text)
        if 1 <= answer_num <= game_data.get("num_options", 12):
            if answer_num == game_data['correct_answer_num']:
                is_correct = True
    except ValueError:
        pass

        # 6. 处理答案 (V4 逻辑)
    if is_correct:
        # 6a. [处理正确答案]
        if user_id not in game_data['correct_players']:
            game_data['correct_players'][user_id] = {'name': user_name}
            is_first_correct_answer = (game_data['first_correct_answer_time'] == 0)
            if is_first_correct_answer:
                game_data['first_correct_answer_time'] = time.time()
                end_game_early = _get_setting_for_group(event, "end_game_after_bonus_time", True)
                bonus_time = _get_setting_for_group(event, "bonus_time_after_first_answer", 5)  # <-- [BUG修正] 修正笔误

                if end_game_early and bonus_time > 0:
                    async def _bonus_time_end_task(sid, delay):
                        await asyncio.sleep(delay)
                        if sid in active_game_sessions:
                            logger.info(f"游戏 {sid} 奖励时间到，提前结束。")
                            await _end_game_session(sid, "奖励时间到！")

                    # asyncio.create_task(_bonus_time_end_task(session_id, bonus_time)) # [BUGFIX]

                    # --- [BUGFIX] 替换为以下内容 ---
                    bonus_task = asyncio.create_task(_bonus_time_end_task(session_id, bonus_time))
                    # 存储奖励任务的引用
                    game_data['bonus_task'] = bonus_task
                    # --- [BUGFIX] 结束 ---

    # 7. [最终结算检查]
    # --- [BUGFIX V3] 开始：修复达到最大次数后不结算的BUG ---
    if max_guess_attempts > 0 and game_data['guess_attempts_count'] >= max_guess_attempts:
        # 无论是否有人答对，达到最大次数都应立即结束游戏

        # 检查游戏是否还活跃 (可能已经被 bonus_task 结束了，虽然概率很低)
        if session_id in active_game_sessions:
            # 统一使用 "已达上限" 消息
            logger.info(f"游戏 {session_id} 达到最大猜测次数，立即结束。")
            await _end_game_session(session_id, f"本轮猜测已达上限({max_guess_attempts}次)！")
    # --- [BUGFIX V3] 结束 ---


# --- (重构) 命令处理器 ---

# 统一的猜歌指令
start_guess_song_unified = on_command(
    "猜歌",
    aliases={
        "gs",
        "猜歌1", "猜歌2", "猜歌3", "猜歌4", "猜歌5", "猜歌6", "猜歌7",
        "gs1", "gs2", "gs3", "gs4", "gs5", "gs6", "gs7"
    },
    priority=10,
    block=True
)


@start_guess_song_unified.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    # 从 event.raw_message (或 Message) 获取指令
    cmd = event.get_plaintext()
    # 适配 `state["_prefix"]["command_str"]` (如果使用 on_command)
    # 为了简单起见，我们直接解析
    match_cmd = re.match(r"^(猜歌|gs)(\d)?", cmd)
    mode_key = 'normal'
    if match_cmd and match_cmd.group(2):
        mode_key = match_cmd.group(2)

    if plugin_config.lightweight_mode and mode_key in ['1', '2']:
        original_mode_name = audio_service.game_modes[mode_key]['name']
        await start_guess_song_unified.finish(f'......轻量模式已启用，模式"{original_mode_name}"已自动切换为普通模式。')

    async with lock:
        can_start, message = await _check_game_start_conditions(event)
        if not can_start:
            if message:
                await start_guess_song_unified.finish(message)
            return

        # 立即设置，防止重复
        active_game_sessions[session_id] = {"placeholder": True, "type": "game_init"}

    # --- [新功能] 发送加载提示 ---
    await start_guess_song_unified.send("正在加载数据……")
    # --- [新功能] 结束 ---

    try:
        initiator_id = get_user_id(event)
        initiator_name = get_user_name(event)
        is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)
        await db_service.consume_daily_play_attempt(initiator_id, initiator_name, session_id, is_independent_limit)

        mode_config = audio_service.game_modes.get(mode_key)
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

        game_data = await audio_service.get_game_clip(**game_kwargs)
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
        game_data['num_options'] = 12  # 告诉答案处理器

        logger.info(f"[猜歌插件] 新游戏开始. 答案: {correct_song['title']} (选项 {game_data['correct_answer_num']})")

        options_img_path = await audio_service.create_options_image(options)

        answer_timeout = _get_setting_for_group(event, "answer_timeout", 30)
        intro_text = f".......嗯\n这首歌是？请在{answer_timeout}秒内发送编号回答。\n"

        # 转换为 Nonebot Message
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
                answer_reveal_messages.append(MessageSegment.image(file=jacket_source))  # URL

        # 运行游戏会话
        await _run_game_session(bot, event, game_data, intro_messages, answer_reveal_messages)

    except Exception as e:
        logger.error(f"游戏启动过程中发生未处理的异常: {e}", exc_info=True)
        await start_guess_song_unified.send("......开始游戏时发生内部错误，已中断。")
        # 确保清理
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()


# --- (重构) 随机猜歌 ---
start_random_guess_song = on_command("随机猜歌", aliases={"rgs"}, priority=10, block=True)


@start_random_guess_song.handle()
async def _(bot: Bot, event: MessageEvent):
    session_id = get_session_id(event)
    lock = game_session_locks[session_id]

    async with lock:
        can_start, message = await _check_game_start_conditions(event)
        if not can_start:
            if message:
                await start_random_guess_song.finish(message)
            return
        active_game_sessions[session_id] = {"placeholder": True, "type": "game_init"}

    # --- [新功能] 发送加载提示 ---
    await start_random_guess_song.send("正在加载数据……")
    # --- [新功能] 结束 ---

    try:
        initiator_id = get_user_id(event)
        initiator_name = get_user_name(event)
        is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)
        await db_service.consume_daily_play_attempt(initiator_id, initiator_name, session_id, is_independent_limit)

        combined_kwargs, total_score, effect_names_display, mode_name_str = audio_service.get_random_mode_config()
        if not combined_kwargs:
            if session_id in active_game_sessions: active_game_sessions.pop(session_id)
            await start_random_guess_song.finish("......随机模式启动失败，没有可用的效果组合。请检查资源文件。")
            return

        await start_random_guess_song.send(f"......本轮应用效果：【{effect_names_display}】(总计{total_score}分)")

        combined_kwargs['random_mode_name'] = f"random_{mode_name_str}"
        combined_kwargs['score'] = total_score
        combined_kwargs['game_type'] = 'guess_song_random'

        game_data = await audio_service.get_game_clip(**combined_kwargs)
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

        options_img_path = await audio_service.create_options_image(options)
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
                answer_reveal_messages.append(MessageSegment.image(file=jacket_source))  # URL

        await _run_game_session(bot, event, game_data, intro_messages, answer_reveal_messages)

    except Exception as e:
        logger.error(f"随机游戏启动过程中发生未处理的异常: {e}", exc_info=True)
        await start_random_guess_song.send("......开始游戏时发生内部错误，已中断。")
    finally:
        if session_id in active_game_sessions and active_game_sessions[session_id].get("type") == "game_init":
            active_game_sessions.pop(session_id)
            last_game_end_time[session_id] = time.time()


# --- (重构) 猜歌手 ---
start_vocalist_game = on_command("猜歌手", priority=10, block=True)


@start_vocalist_game.handle()
async def _(bot: Bot, event: MessageEvent):
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

    # --- [新功能] 发送加载提示 ---
    await start_vocalist_game.send("正在加载数据……")
    # --- [新功能] 结束 ---

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

        game_data = await audio_service.get_game_clip(
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

        # 辅助函数 (从 main.py 迁移)
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
                intro_messages.append(MessageSegment.image(file=jacket_source))  # URL

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


# --- (重构) 帮助 ---
show_guess_song_help = on_command("猜歌帮助", priority=10, block=True)


@show_guess_song_help.handle()
async def _(bot: Bot, event: MessageEvent):
    if not await _is_group_allowed(event):
        return

    img_path = await audio_service.draw_help_image()
    if img_path:
        img_p = Path(img_path)
        await show_guess_song_help.send(MessageSegment.image(file=img_p.absolute().as_uri()))
    else:
        await show_guess_song_help.send("生成帮助图片时出错。")


# --- (重构) 听歌指令 ---

async def _handle_listen_command(matcher: Matcher, bot: Bot, event: MessageEvent, mode: str,
                                 search_term: Optional[str]):
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

        config = audio_service.listen_modes[mode]
        if not getattr(cache_service, config['list_attr']):
            await matcher.finish(config['not_found_msg'])

        # 标记会话
        active_game_sessions[session_id] = {"placeholder": True, "type": "listen"}

    # --- [新功能] 发送加载提示 ---
    await matcher.send("正在加载数据……")
    # --- [新功能] 结束 ---

    try:
        song_to_play, mp3_source = await audio_service.get_listen_song_and_path(mode, search_term)

        if not song_to_play or not mp3_source:
            no_match_msg = audio_service.listen_modes[mode]['no_match_msg'].format(
                search_term=search_term) if search_term else "......出错了，找不到有效的音频文件。"
            await matcher.finish(no_match_msg)
            return

        jacket_source = cache_service.get_resource_path_or_url(
            f"music_jacket/{song_to_play['jacketAssetbundleName']}.png")

        msg_chain = Message(f"歌曲:{song_to_play['id']}. {song_to_play['title']} {config['title_suffix']}\n")
        if jacket_source:
            if isinstance(jacket_source, Path):
                msg_chain.append(MessageSegment.image(file=jacket_source.absolute().as_uri()))
            else:
                msg_chain.append(MessageSegment.image(file=jacket_source))  # URL

        await matcher.send(msg_chain)

        if isinstance(mp3_source, Path):
            await matcher.send(MessageSegment.record(file=mp3_source.absolute().as_uri()))
        else:
            await matcher.send(MessageSegment.record(file=mp3_source))  # URL

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
    # 使用偏函数来捕获 mode 变量
    def create_handler(current_mode: str):
        async def handler(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
            search_term = args.extract_plain_text().strip() or None
            await _handle_listen_command(matcher, bot, event, current_mode, search_term)

        return handler


    on_command(cmd, priority=10, block=True).handle()(create_handler(mode))

# --- (重构) 听anvo 指令 ---
listen_anvo = on_command("听anvo", aliases={"anvo", "listen_anvo", "anov", "listen_anov", "听anov"}, priority=10,
                         block=True)


@listen_anvo.handle()
async def _(matcher: Matcher, bot: Bot, event: MessageEvent, args: Message = CommandArg()):
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

    # --- [新功能] 发送加载提示 ---
    await matcher.send("正在加载数据……")
    # --- [新功能] 结束 ---

    try:
        content = args.extract_plain_text().strip()

        song_to_play, vocal_info = await audio_service.get_anvo_song_and_vocal(
            content,
            cache_service.another_vocal_songs,
            cache_service.char_id_to_anov_songs,
            cache_service.abbr_to_char_id
        )

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
            # List versions only
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

        mp3_source_path = await audio_service.process_anvo_audio(song_to_play, vocal_info)

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
                msg_chain.append(MessageSegment.image(file=jacket_source.absolute().as_uri()))
            else:
                msg_chain.append(MessageSegment.image(file=jacket_source))  # URL

        await matcher.send(msg_chain)
        await matcher.send(MessageSegment.record(file=mp3_source.absolute().as_uri()))

        user_id = get_user_id(event)
        await db_service.record_listen_song(user_id, get_user_name(event))

    except Exception as e:
        logger.error(f"处理听anvo功能时出错: {e}", exc_info=True)
        await matcher.send("......播放时出错了，请联系管理员。")
    finally:
        if session_id in active_game_sessions:
            active_game_sessions.pop(session_id)
        last_game_end_time[session_id] = time.time()