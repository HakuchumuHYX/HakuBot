import json
import asyncio
import re
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from typing import Callable, TypeVar, Any
from nonebot.log import logger
from core.access import is_feature_enabled
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import (
    AnalysisResult,
    GroupStatistics,
    SummaryTopic,
    UserTitle,
    GoldenQuote,
    TokenUsage,
    EmojiStatistics,
)
from plugins.group_daily_analysis.visualization.charts import ActivityVisualizer
from plugins.group_daily_analysis.utils.llm import call_chat_completion
from utils.llm.client import is_retryable_llm_error
from plugins.group_daily_analysis.analysis.context import (
    TranscriptContext,
    build_transcript_context,
)
from plugins.group_daily_analysis.analysis.fallbacks import (
    build_golden_quote_fallback,
    build_topic_fallback,
    build_user_title_fallback,
)
from plugins.group_daily_analysis.analysis.schemas import (
    TopicsPayload,
    UserTitlesPayload,
    GoldenQuotesPayload,
    TopicsAndQuotesPayload,
)
from plugins.group_daily_analysis.analysis.analyzers.common import parse_payload_items

T = TypeVar("T")
from plugins.group_daily_analysis.analysis.prompts import safe_prompt_format


class TitlesAnalysis:
    async def _analyze_user_titles_safe(
        self, ctx: TranscriptContext, max_titles: int
    ) -> tuple[list[UserTitle], TokenUsage]:
        """
        用户称号分析（结构化特征版）

        不再把全天聊天原文塞进称号 prompt，而是使用本地统计出的用户特征和少量代表发言。
        这能显著降低 token，也避免称号模块因长输出/空 JSON 导致整块缺失。
        """
        top_users = sorted(
            ctx.user_features.values(),
            key=lambda item: (
                item.message_count,
                item.character_count,
                item.emoji_count,
            ),
            reverse=True,
        )[:max_titles]

        if not top_users:
            logger.warning("没有活跃用户，跳过称号分析")
            return [], TokenUsage()

        users_text = "\n".join(
            [
                (
                    f"- {u.name} (qq: {u.user_id}, 消息数: {u.message_count}, "
                    f"总字数: {u.character_count}, 平均长度: {u.average_length:.1f}, "
                    f"表情数: {u.emoji_count}, @次数: {u.at_count}, "
                    f"深夜发言: {u.night_message_count}, 代表发言: "
                    f"{' / '.join(u.samples[:3])})"
                )
                for u in top_users
            ]
        )

        base_prompt = safe_prompt_format(
            plugin_config.user_title_analysis_prompt,
            users_text=users_text,
            max_user_titles=max_titles,
        )

        prompt = (
            "以下是群聊中最活跃的用户结构化特征。请只基于这些候选用户生成称号，"
            "不要编造不存在的用户。\n\n"
            f"{base_prompt}\n\n"
            + self._json_object_tail(
                '{"items":[{"name":"用户名","qq":123456,"title":"称号","reason":"理由","personality":"人物画像"}]}'
            )
        )

        # 不再 catch 异常 - 由上层 _run_subtask_with_retry 负责重试
        content, tokens = await call_chat_completion(
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
            max_tokens=plugin_config.llm.max_tokens,
        )
        data = parse_payload_items(content, UserTitlesPayload, module_name="用户称号")

        features_by_name = {}
        for u in top_users:
            features_by_name[u.name] = u
            features_by_name[u.user_id] = u

        # 尝试补齐 qq（如果 LLM 没给）
        user_titles: list[UserTitle] = []
        for item in data:
            item_dict = item.model_dump()
            name = item_dict.get("name", "")
            qq = item_dict.get("qq", 0)

            if not qq:
                u = features_by_name.get(name)
                if u and str(u.user_id).isdigit():
                    qq = int(u.user_id)

            user_titles.append(
                UserTitle(
                    name=name,
                    qq=qq or None,
                    title=item_dict.get("title", ""),
                    reason=item_dict.get("reason", ""),
                    personality=item_dict.get("personality", ""),
                )
            )

        logger.info(f"用户称号分析完成，生成了 {len(user_titles)} 个称号")
        return user_titles[:max_titles], tokens
