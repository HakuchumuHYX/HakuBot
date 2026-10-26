"""转写和统计、三块 LLM 分析（话题、称号、金句）"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar

from json_repair import repair_json
from nonebot import logger
from openai import APIError
from pydantic import BaseModel

from core.access import is_enabled
from utils.llm.client import ChatResult, LLMClientConfig, TokenUsage, chat_completion
from utils.logging import get_exc_desc
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import (
    AnalysisResult,
    GoldenQuote,
    Statistics,
    SummaryTopic,
    UserTitle,
)

ItemT = TypeVar("ItemT", bound=BaseModel)

EMOJI_TYPES = {"face", "mface", "bface", "sface"}
SECTION_NAMES = {"topics": "话题", "user_titles": "用户称号", "golden_quotes": "金句"}

SYSTEM_PROMPT = (
    "你是群聊日报分析助手。请严格基于用户提供的群聊记录分析，"
    "不要编造群友、发言或不存在的结论。输出必须是 JSON。"
)
# 聊天记录单独放在前一条 user message，提示词里的 {messages_text} 换成这句
TRANSCRIPT_HINT = "聊天记录已在本次 API 请求的前一条 user message 中提供，请基于该消息中的完整记录分析。"


# --- 转写和统计 ---


@dataclass
class UserFeature:
    name: str
    message_count: int = 0
    character_count: int = 0
    emoji_count: int = 0
    at_count: int = 0
    night_message_count: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass
class Transcript:
    lines: list[str] = field(default_factory=list)
    users: dict[str, UserFeature] = field(default_factory=dict)


def _is_emoji(seg: dict) -> bool:
    return seg["type"] in EMOJI_TYPES or (
        seg["type"] == "image" and "表情" in seg["data"].get("summary", "")
    )


def read_messages(rows) -> tuple[Statistics, Transcript]:
    """统计算全部消息；转写只留有文字的、去掉 bot 和命令"""
    stats = Statistics(message_count=len(rows))
    transcript = Transcript()
    participants = set()
    bot_ids = set(plugin_config.bot_qq_ids)

    for row in rows:
        user_id = row["user_id"]
        sent_at = datetime.fromtimestamp(row["timestamp"])
        segments = json.loads(row["raw_message"])
        participants.add(user_id)
        stats.hourly[sent_at.hour] += 1
        stats.emoji_count += sum(map(_is_emoji, segments))

        parts = []
        at_count = 0
        for seg in segments:
            if seg["type"] == "text":
                parts.append(seg["data"]["text"])
                stats.total_characters += len(seg["data"]["text"])
            elif seg["type"] == "at":
                parts.append(f"@{seg['data'].get('qq', '')}")
                at_count += 1

        content = "".join(parts).strip().replace('"', "'").replace("\n", " ")
        if user_id in bot_ids or len(content) <= 1 or content.startswith("/"):
            continue

        name = row["sender_name"].strip() or "群友"
        user = transcript.users.setdefault(user_id, UserFeature(name=name))
        user.name = name
        user.message_count += 1
        user.character_count += len(content)
        user.emoji_count += sum(map(_is_emoji, segments))
        user.at_count += at_count
        if sent_at.hour >= 23 or sent_at.hour < 6:
            user.night_message_count += 1
        if len(user.samples) < 5:
            user.samples.append(content)
        transcript.lines.append(f"[{sent_at:%H:%M}] {name}: {content}")

    stats.participant_count = len(participants)
    return stats, transcript


def _truncate(lines: list[str]) -> list[str]:
    """超过 max_input_length 就从最早的消息开始丢"""
    total = 0
    for i in range(len(lines) - 1, -1, -1):
        total += len(lines[i]) + 1
        if total > plugin_config.max_input_length:
            logger.warning(
                f"转写超过 {plugin_config.max_input_length} 字，丢掉最早的 {i + 1} 条消息"
            )
            return lines[i + 1 :]
    return lines


# --- LLM ---


def _format_prompt(prompt: str, **values) -> str:
    # 提示词里有 JSON 示例，str.format 会被花括号绊倒，只替换认识的占位符
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt


def _json_tail(example: str) -> str:
    return (
        "\n\n---\n\n"
        "## 最高优先级输出格式要求\n"
        "本次调用要求返回 JSON object，这条要求覆盖上方模板里任何“返回 JSON 数组”的旧示例。\n"
        "不要输出 markdown，不要输出解释，不要输出顶层数组。\n"
        f"唯一允许的顶层格式示例：{example}\n"
        "其中 items 必须是数组。"
    )


def _parse_items(
    result: ChatResult, item_model: type[ItemT]
) -> tuple[list[ItemT], dict]:
    """返回 items 和整个顶层 object（话题那次还要取 headline）"""
    # json_repair 会把截断的 JSON 补成合法的，不先拦住就会悄悄少几条
    if result.finish_reason == "length":
        raise ValueError("输出被 max_tokens 截断")
    data = json.loads(repair_json(result.content, return_objects=False))
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        raise ValueError(f"返回的 JSON 不是 object: {type(data).__name__}")
    items = [item_model.model_validate(item) for item in data.get("items") or []]
    if not items:
        raise ValueError("返回空 items")
    return items, data


async def _ask(
    section: str,
    messages: list[dict],
    item_model: type[ItemT],
    temperature: float,
    usage: TokenUsage,
) -> tuple[list[ItemT], dict]:
    """输出无效或被截断时重试一次；网络错误由 client 重试"""
    llm = plugin_config.llm
    config = LLMClientConfig(
        api_key=llm.api_key,
        base_url=llm.base_url,
        model=llm.model,
        timeout=llm.timeout,
        proxy=llm.proxy,
        max_tokens=llm.max_tokens,
        thinking_enabled=llm.thinking_enabled,
        reasoning_effort=llm.reasoning_effort,
        extra_body=llm.extra_body,
    )
    for attempt in range(2):
        result = await chat_completion(
            config,
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        usage.prompt_tokens += result.usage.prompt_tokens
        usage.completion_tokens += result.usage.completion_tokens
        usage.total_tokens += result.usage.total_tokens
        try:
            return _parse_items(result, item_model)
        except ValueError as e:
            if attempt:
                raise
            logger.info(f"{section}输出无效，重试一次: {e}")


def _with_transcript(transcript_text: str, prompt: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"以下是今日群聊记录：\n\n{transcript_text}"},
        {"role": "user", "content": prompt},
    ]


async def _topics(
    transcript_text: str, limit: int, usage: TokenUsage
) -> tuple[list[SummaryTopic], str]:
    """话题和本集标题（headline）一次问完"""
    prompt = _format_prompt(
        plugin_config.topic_analysis_prompt,
        max_topics=limit,
        messages_text=TRANSCRIPT_HINT,
    ) + _json_tail(
        '{"headline":"本集标题","items":[{"topic":"话题名称","time":"09:12",'
        '"contributors":["用户1"],"detail":"描述"}]}'
    )
    items, data = await _ask(
        SECTION_NAMES["topics"],
        _with_transcript(transcript_text, prompt),
        SummaryTopic,
        0.3,
        usage,
    )
    for topic in items:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", topic.time):
            topic.time = ""
    headline = data.get("headline")
    return items[:limit], headline.strip() if isinstance(headline, str) else ""


async def _golden_quotes(
    transcript_text: str,
    transcript: Transcript,
    limit: int,
    usage: TokenUsage,
):
    prompt = _format_prompt(
        plugin_config.golden_quote_analysis_prompt,
        max_golden_quotes=limit,
        messages_text=TRANSCRIPT_HINT,
    ) + _json_tail(
        '{"items":[{"content":"金句原文","sender":"发言人","reason":"辣评"}]}'
    )
    quotes, _ = await _ask(
        SECTION_NAMES["golden_quotes"],
        _with_transcript(transcript_text, prompt),
        GoldenQuote,
        1.1,
        usage,
    )

    # 提示词不要 QQ 号和时间，按发言人名字和原文在转写里查
    qq_by_name = {u.name: int(user_id) for user_id, u in transcript.users.items()}
    for quote in quotes:
        quote.qq = qq_by_name.get(quote.sender)
        # 转写每行是 "[HH:MM] 名字: 内容"
        speaker = f"] {quote.sender}: "
        quote.time = next(
            (
                line[1:6]
                for line in transcript.lines
                if speaker in line and quote.content in line.split(speaker, 1)[1]
            ),
            "",
        )
    return quotes[:limit]


async def _user_titles(users: dict[str, UserFeature], limit: int, usage: TokenUsage):
    """只给最活跃的人的统计特征和几句代表发言，不给全天原文"""
    top_users = sorted(
        users.items(),
        key=lambda item: (
            item[1].message_count,
            item[1].character_count,
            item[1].emoji_count,
        ),
        reverse=True,
    )[:limit]
    users_text = "\n".join(
        f"- {u.name} (qq: {user_id}, 消息数: {u.message_count}, "
        f"总字数: {u.character_count}, 平均长度: {u.character_count / u.message_count:.1f}, "
        f"表情数: {u.emoji_count}, @次数: {u.at_count}, "
        f"深夜发言: {u.night_message_count}, 代表发言: {' / '.join(u.samples[:3])})"
        for user_id, u in top_users
    )
    prompt = (
        "以下是群聊中最活跃的用户结构化特征。请只基于这些候选用户生成称号，"
        "不要编造不存在的用户。\n\n"
        + _format_prompt(
            plugin_config.user_title_analysis_prompt,
            users_text=users_text,
            max_user_titles=limit,
        )
        + "\n\n"
        + _json_tail(
            '{"items":[{"name":"用户名","qq":123456,"title":"称号","reason":"理由","personality":"人物画像"}]}'
        )
    )
    titles, _ = await _ask(
        SECTION_NAMES["user_titles"],
        [{"role": "user", "content": prompt}],
        UserTitle,
        0.7,
        usage,
    )

    # LLM 没给 QQ 号就按名字补，渲染时要拿头像；发言条数按 QQ 号对回转写统计
    qq_by_name = {u.name: int(user_id) for user_id, u in top_users}
    for title in titles:
        title.qq = title.qq or qq_by_name.get(title.name)
        feature = users.get(str(title.qq))
        title.message_count = feature.message_count if feature else 0
    return titles[:limit]


async def analyze(group_id: int, rows) -> AnalysisResult:
    """三块各自失败就在日报里省略；开着的全失败才算整份失败"""
    stats, transcript = read_messages(rows)
    result = AnalysisResult(statistics=stats)
    if not transcript.lines:
        logger.warning(f"群 {group_id} 没有有效的文本消息用于分析")
        return result

    # 消息越多给的条数越多：每 500 条加 1
    bonus = len(transcript.lines) // 500
    transcript_text = "\n".join(_truncate(transcript.lines))
    jobs = {}
    if is_enabled("group_daily_analysis:topics", str(group_id)):
        jobs["topics"] = _topics(
            transcript_text, min(10, plugin_config.max_topics + bonus), result.usage
        )
    if is_enabled("group_daily_analysis:user_titles", str(group_id)):
        jobs["user_titles"] = _user_titles(
            transcript.users,
            min(15, plugin_config.max_user_titles + bonus),
            result.usage,
        )
    if is_enabled("group_daily_analysis:golden_quotes", str(group_id)):
        jobs["golden_quotes"] = _golden_quotes(
            transcript_text,
            transcript,
            min(10, plugin_config.max_golden_quotes + bonus),
            result.usage,
        )

    outcomes = await asyncio.gather(*jobs.values(), return_exceptions=True)
    failed = []
    for key, outcome in zip(jobs, outcomes):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, APIError):
            # 上游故障（503、超时等），client 已重试过，调用栈没有排查价值
            logger.warning(
                f"群 {group_id} {SECTION_NAMES[key]}分析失败，日报省略这一块: "
                f"{get_exc_desc(outcome)}"
            )
            failed.append(key)
        elif isinstance(outcome, Exception):
            logger.opt(exception=outcome).warning(
                f"群 {group_id} {SECTION_NAMES[key]}分析失败，日报省略这一块"
            )
            failed.append(key)
        elif key == "topics":
            result.topics, result.headline = outcome
        else:
            setattr(result, key, outcome)
    if jobs and len(failed) == len(jobs):
        raise RuntimeError(f"群 {group_id} 日报分析全部失败")

    states = []
    for key, name in SECTION_NAMES.items():
        if key not in jobs:
            states.append(f"{name}=关闭")
        elif key in failed:
            states.append(f"{name}=失败")
        else:
            states.append(f"{name}={len(getattr(result, key))}")
    usage = result.usage
    logger.info(
        f"群 {group_id} 分析完成: 消息={stats.message_count}, {', '.join(states)}, "
        f"Token={usage.total_tokens} (P:{usage.prompt_tokens}/C:{usage.completion_tokens})"
    )
    return result
