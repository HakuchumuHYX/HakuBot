from .context import TranscriptContext, UserFeature


def _choose_title(feature: UserFeature) -> tuple[str, str]:
    if feature.emoji_count >= 2:
        return "表情包军火库", f"发送了 {feature.emoji_count} 个表情，聊天存在感很强。"
    if feature.night_message_count >= max(1, feature.message_count // 2):
        return "夜猫子", f"深夜发言 {feature.night_message_count} 次，明显还在熬夜冲浪。"
    if feature.average_length >= 30:
        return "评论家", f"平均发言长度 {feature.average_length:.1f} 字，输出欲很稳定。"
    return "龙王", f"发言 {feature.message_count} 条，是今天最活跃的群友之一。"


def _build_personality(feature: UserFeature) -> str:
    sample = f"代表发言像是“{feature.samples[0]}”" if feature.samples else "今天存在感比较稳定"
    if feature.emoji_count >= 2:
        return f"这个人属于群里的气氛按钮，文字可能没几句，但表情包一出场就知道他在线。{sample}，属于靠节奏和反应速度刷存在感的类型。"
    if feature.night_message_count >= max(1, feature.message_count // 2):
        return f"昼伏夜出型群友，别人准备睡了他刚开始上线，深夜聊天区常驻居民。{sample}，看起来很适合负责群聊的夜间值班。"
    if feature.average_length >= 30:
        return f"输出欲很稳定，别人发一句他能接一小段，属于群聊里的长文补丁。{sample}，一旦进入话题就很容易把聊天从水群拉成研讨会。"
    return f"今天存在感很强，几乎每个热闹片段都能看到他冒头，是群聊流速的重要组成部分。{sample}，主打一个随时在线、随时接话。"


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
                "personality": _build_personality(feature),
            }
        )

    return results


def build_topic_fallback(ctx: TranscriptContext, max_topics: int) -> list[dict]:
    if not ctx.text_messages or max_topics <= 0:
        return []

    ranked_users = sorted(
        ctx.user_features.values(),
        key=lambda item: (item.message_count, item.character_count),
        reverse=True,
    )
    contributors = []
    for feature in ranked_users:
        if feature.name not in contributors:
            contributors.append(feature.name)
        if len(contributors) >= 5:
            break

    message_count = len(ctx.text_messages)
    participant_count = len(ctx.user_features)
    first_samples = "；".join(msg.content for msg in ctx.text_messages[:3])
    hot_samples = "；".join(candidate.content for candidate in ctx.quote_candidates[:3])

    topics = [
        {
            "topic": "今日群聊概览",
            "contributors": contributors,
            "detail": f"今天共有 {participant_count} 位群友参与，有效文本消息 {message_count} 条，群聊整体没有冷场。开场附近的代表内容包括：{first_samples}。虽然这是本地兜底总结，但至少能看出今天群里不是一潭死水。",
        }
    ]

    if ranked_users:
        top = ranked_users[0]
        topics.append(
            {
                "topic": "高频发言现场",
                "contributors": [u.name for u in ranked_users[:5]],
                "detail": f"{top.name} 今天发言 {top.message_count} 条，总字数 {top.character_count}，属于存在感很难忽略的那种群友。活跃用户们把聊天流速撑了起来，哪怕 LLM 分析暂时掉线，日报也不能假装他们没来过。",
            }
        )

    if hot_samples:
        topics.append(
            {
                "topic": "代表发言摘录",
                "contributors": contributors,
                "detail": f"本地候选里比较有冲击力的发言包括：{hot_samples}。这些内容不一定是全天最完整的话题，但至少能代表今天群聊的精神波动。",
            }
        )

    return topics[:max_topics]


def _fallback_quote_reason(content: str) -> str:
    if "？" in content or "?" in content:
        return "问号一出，群聊 CPU 先烧一半。"
    if "草" in content or "笑死" in content:
        return "这句自带笑点，属于发出来就不用解释的类型。"
    if len(content) >= 40:
        return "一本正经地离谱，杀伤力主要来自过于自信。"
    return "这句有点东西，放日报里刚好当精神污染样本。"


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
        item = {
            "content": content,
            "sender": candidate.sender,
            "reason": _fallback_quote_reason(content),
        }
        if candidate.user_id.isdigit():
            item["qq"] = int(candidate.user_id)
        results.append(item)
        if len(results) >= max_quotes:
            break
    return results
