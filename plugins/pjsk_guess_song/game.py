"""一局猜歌：占用与冷却、出题（猜歌 / 猜歌手）、答题、结算"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Awaitable, Callable

from nonebot import get_bot, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.matcher import Matcher

from core.lifecycle import runtime
from utils.onebot.media import audio_segment, image_segment
from plugins.pjsk_guess_song import audio, render, songs, store
from plugins.pjsk_guess_song.config import settings

# 随机到的歌下载失败时换一首（资源站可能还没有新曲）
SONG_ATTEMPTS = 3
VOCALIST_SPEED = 1.5


@dataclass
class Question:
    clip: bytes
    intro: Message
    answer: int
    reveal: Message


@dataclass
class Round:
    group_id: int
    bot_id: str
    answer: int
    reveal: Message
    # user_id → 群名片
    correct: dict[str, str] = field(default_factory=dict)
    guess_counts: dict[str, int] = field(default_factory=dict)
    timeout_task: asyncio.Task | None = None
    bonus_task: asyncio.Task | None = None


_rounds: dict[int, Round] = {}
# 正在出题或正在听歌的群
_busy: set[int] = set()
_last_end: dict[int, float] = {}


def user_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


def occupy(group_id: int) -> str | None:
    """冷却已过且本群空闲时占住本群并返回 None，否则返回提示语；占住后必须 release"""
    remaining = settings.game_cooldown_seconds - (
        time.time() - _last_end.get(group_id, 0)
    )
    if remaining > 0:
        shown = f"{remaining:.3f}" if remaining < 1 else str(int(remaining))
        return f"嗯......休息 {shown} 秒再玩吧......"
    if group_id in _busy or group_id in _rounds:
        return "......有一个正在进行的游戏或播放任务了呢。"
    _busy.add(group_id)
    return None


def release(group_id: int) -> None:
    _busy.discard(group_id)
    _last_end[group_id] = time.time()


def current(group_id: int) -> Round | None:
    return _rounds.get(group_id)


def _disabled_message() -> str | None:
    now = datetime.now().time()
    for period in settings.disable_guess_song_periods:
        if period.start <= now < period.end:
            return (
                period.message
                or f"当前时段 ({period.start:%H:%M} - {period.end:%H:%M}) 猜歌功能已禁用。"
            )
    return None


# ============== 出题 ==============


async def song_question() -> Question | None:
    """随机一首，优先 sekai 版，12 选 1"""
    for _ in range(SONG_ATTEMPTS):
        song = random.choice(songs.songs)
        vocal = song.vocal("sekai") or (
            random.choice(song.vocals) if song.vocals else None
        )
        if vocal is None:
            continue
        try:
            clip = await audio.clip(
                vocal.bundle, song.filler_sec, settings.clip_duration_seconds
            )
            break
        except Exception:
            logger.opt(exception=True).warning(
                f"猜歌：{song.title} 片段生成失败，换一首"
            )
    else:
        return None

    options = random.sample([s for s in songs.songs if s.id != song.id], 11) + [song]
    random.shuffle(options)
    answer = options.index(song) + 1
    logger.info(f"猜歌：新一局答案 {answer}. {song.title}")

    intro = Message(
        f".......嗯\n这首歌是？请在{settings.answer_timeout}秒内发送编号回答。\n"
    )
    intro.append(image_segment(await render.options_image(options)))
    reveal = Message(f"正确答案是: {answer}. {song.title}\n")
    reveal.append(image_segment(songs.jacket_url(song.jacket)))
    return Question(clip, intro, answer, reveal)


async def vocalist_question() -> Question:
    """随机一首的随机一个 another_vocal 版本，1.5 倍速，猜是谁唱的"""
    song = random.choice(songs.anvo_songs)
    choices = song.vocals_of("another_vocal")
    correct = random.choice(choices)
    clip = await audio.clip(
        correct.bundle, song.filler_sec, settings.clip_duration_seconds, VOCALIST_SPEED
    )

    random.shuffle(choices)
    answer = choices.index(correct) + 1
    lines = "".join(
        f"{i}. {songs.vocalist_name(v)}\n" for i, v in enumerate(choices, 1)
    )
    intro = Message(
        f"这首歌是【{song.title}】，正在演唱的是谁？[{VOCALIST_SPEED}倍速]\n"
        f"请在{settings.answer_timeout}秒内发送编号回答。\n\n⚠️ 测试功能\n\n{lines}"
    )
    intro.append(image_segment(songs.jacket_url(song.jacket)))
    reveal = Message(f"正确答案是: {answer}. {songs.vocalist_name(correct)}")
    return Question(clip, intro, answer, reveal)


# ============== 开局 / 答题 / 结算 ==============


async def start(
    matcher: type[Matcher],
    bot: Bot,
    event: GroupMessageEvent,
    make_question: Callable[[], Awaitable[Question | None]],
) -> None:
    group_id, user_id = event.group_id, str(event.user_id)
    if message := _disabled_message():
        await matcher.send(message)
        return

    scope = str(group_id) if settings.independent_daily_limit else ""
    limit = settings.daily_play_limit
    if await store.daily_count(user_id, scope, "play") >= limit:
        who = "你今天在本群" if settings.independent_daily_limit else "你今天"
        await matcher.send(
            f"......{who}的游戏次数已达上限（{limit}次），请明天再来吧......"
        )
        return

    if message := occupy(group_id):
        await matcher.send(message)
        return
    try:
        await matcher.send("正在加载数据……")
        try:
            question = await make_question()
        except Exception:
            logger.exception("猜歌：出题失败")
            question = None
        if question is None:
            await matcher.send("......开始游戏失败了，过会儿再试吧。")
            return
        # 出题成功才扣次数
        await store.bump_daily(user_id, scope, "play")

        await matcher.send(audio_segment(question.clip))
        await matcher.send(question.intro)
        rnd = Round(group_id, bot.self_id, question.answer, question.reveal)
        _rounds[group_id] = rnd
        rnd.timeout_task = runtime.spawn(
            _finish_later(rnd, settings.answer_timeout, "时间到！"),
            name="pjsk_guess_song",
        )
    finally:
        _busy.discard(group_id)
        if group_id not in _rounds:
            _last_end[group_id] = time.time()


def guess(rnd: Round, event: GroupMessageEvent, number: int) -> str | None:
    """记一次作答；需要回复本人时返回回复内容"""
    user_id = str(event.user_id)
    count = rnd.guess_counts.get(user_id, 0)
    if 0 < settings.max_guess_attempts <= count:
        return "您的猜歌次数已用完"
    rnd.guess_counts[user_id] = count + 1

    if number != rnd.answer or user_id in rnd.correct:
        return None
    first = not rnd.correct
    rnd.correct[user_id] = user_name(event)
    bonus = settings.bonus_time_after_first_answer
    if first and bonus > 0:
        rnd.bonus_task = runtime.spawn(
            _finish_later(rnd, bonus, "奖励时间到！"), name="pjsk_guess_song"
        )
    return None


async def _finish_later(rnd: Round, delay: float, reason: str) -> None:
    await asyncio.sleep(delay)
    await _finish(rnd, reason)


async def _finish(rnd: Round, reason: str) -> None:
    if _rounds.get(rnd.group_id) is not rnd:
        return
    del _rounds[rnd.group_id]
    _last_end[rnd.group_id] = time.time()
    this_task = asyncio.current_task()
    for task in (rnd.timeout_task, rnd.bonus_task):
        if task is not None and task is not this_task:
            task.cancel()

    for user_id, name in rnd.correct.items():
        await store.add_score(str(rnd.group_id), user_id, name)
    if rnd.correct:
        summary = f"{reason}\n本轮答对的玩家有：\n{'、'.join(rnd.correct.values())}"
    else:
        summary = f"{reason} 好像......没有人答对......"

    bot = get_bot(rnd.bot_id)
    await bot.send_group_msg(group_id=rnd.group_id, message=summary)
    await bot.send_group_msg(group_id=rnd.group_id, message=rnd.reveal)
