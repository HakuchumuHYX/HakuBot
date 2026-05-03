from .context import TranscriptContext, UserFeature


def _choose_title(feature: UserFeature) -> tuple[str, str]:
    if feature.emoji_count >= 2:
        return "表情包军火库", f"发送了 {feature.emoji_count} 个表情，聊天存在感很强。"
    if feature.night_message_count >= max(1, feature.message_count // 2):
        return "夜猫子", f"深夜发言 {feature.night_message_count} 次，明显还在熬夜冲浪。"
    if feature.average_length >= 30:
        return "评论家", f"平均发言长度 {feature.average_length:.1f} 字，输出欲很稳定。"
    return "龙王", f"发言 {feature.message_count} 条，是今天最活跃的群友之一。"


def build_user_title_fallback(ctx: TranscriptContext, max_titles: int) -> list[dict]:
    ranked = sorted(
        ctx.user_features.values(),
        key=lambda item: (item.message_count, item.character_count, item.emoji_count),
        reverse=True,
    )
    results: list[dict] = []
    used_titles: set[str] = set()

    for feature in ranked[:max_titles]:
        title, reason = _choose_title(feature)
        if title in used_titles:
            title = "活跃群友"
            reason = f"今天发言 {feature.message_count} 条，参与度靠前。"
        used_titles.add(title)

        qq = int(feature.user_id) if feature.user_id.isdigit() else None
        results.append(
            {
                "name": feature.name,
                "qq": qq,
                "title": title,
                "reason": reason,
            }
        )

    return results


def build_topic_fallback(ctx: TranscriptContext, max_topics: int) -> list[dict]:
    if not ctx.text_messages or max_topics <= 0:
        return []

    contributors = []
    for feature in sorted(
        ctx.user_features.values(),
        key=lambda item: (item.message_count, item.character_count),
        reverse=True,
    ):
        if feature.name not in contributors:
            contributors.append(feature.name)
        if len(contributors) >= 5:
            break

    message_count = len(ctx.text_messages)
    participant_count = len(ctx.user_features)
    samples = "；".join(msg.content for msg in ctx.text_messages[:3])
    return [
        {
            "topic": "今日群聊概览",
            "contributors": contributors,
            "detail": (
                f"本地兜底统计显示，今日共有 {participant_count} 位群友参与，"
                f"有效文本消息 {message_count} 条。代表内容包括：{samples}"
            ),
        }
    ][:max_topics]


def build_golden_quote_fallback(ctx: TranscriptContext, max_quotes: int) -> list[dict]:
    if max_quotes <= 0:
        return []

    results: list[dict] = []
    seen: set[str] = set()
    for candidate in ctx.quote_candidates:
        content = candidate.content.strip()
        if not content or content in seen:
            continue
        seen.add(content)
        results.append(
            {
                "content": content,
                "sender": candidate.sender,
                "reason": "LLM 金句分析失败时的本地候选，按表达强度和互动信号保留。",
            }
        )
        if len(results) >= max_quotes:
            break
    return results
