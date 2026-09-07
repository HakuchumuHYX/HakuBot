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


class StatisticsAnalysis:
    def _calculate_statistics(self, messages: list) -> GroupStatistics:
        total_chars = 0
        participants = set()
        emoji_stats = EmojiStatistics()

        for msg in messages:
            sender = msg.get("sender", {})
            uid = sender.get("user_id") or sender.get("nickname", "unknown")
            participants.add(uid)

            for seg in msg.get("message", []):
                if seg["type"] == "text":
                    total_chars += len(seg["data"].get("text", ""))
                elif seg["type"] == "face":
                    # QQ基础表情
                    emoji_stats.face_count += 1
                    face_id = seg["data"].get("id", "unknown")
                    emoji_stats.face_details[f"face_{face_id}"] = (
                        emoji_stats.face_details.get(f"face_{face_id}", 0) + 1
                    )
                elif seg["type"] == "mface":
                    # 动画表情/魔法表情
                    emoji_stats.mface_count += 1
                    emoji_id = seg["data"].get("emoji_id", "unknown")
                    emoji_stats.face_details[f"mface_{emoji_id}"] = (
                        emoji_stats.face_details.get(f"mface_{emoji_id}", 0) + 1
                    )
                elif seg["type"] == "bface":
                    # 超级表情
                    emoji_stats.bface_count += 1
                    emoji_id = seg["data"].get("p", "unknown")
                    emoji_stats.face_details[f"bface_{emoji_id}"] = (
                        emoji_stats.face_details.get(f"bface_{emoji_id}", 0) + 1
                    )
                elif seg["type"] == "sface":
                    # 小表情
                    emoji_stats.sface_count += 1
                    emoji_id = seg["data"].get("id", "unknown")
                    emoji_stats.face_details[f"sface_{emoji_id}"] = (
                        emoji_stats.face_details.get(f"sface_{emoji_id}", 0) + 1
                    )
                elif seg["type"] == "image":
                    # 检查是否是动画表情（通过summary字段判断）
                    data = seg.get("data", {})
                    summary = data.get("summary", "")
                    if "动画表情" in summary or "表情" in summary:
                        emoji_stats.mface_count += 1
                        file_name = data.get("file", "unknown")
                        emoji_stats.face_details[f"animated_{file_name}"] = (
                            emoji_stats.face_details.get(f"animated_{file_name}", 0) + 1
                        )
                elif (
                    seg["type"] in ["record", "video"]
                    and "emoji" in str(seg.get("data", {})).lower()
                ):
                    # 其他可能的表情类型
                    emoji_stats.other_emoji_count += 1

        # Most active period & Visualization
        viz = self.activity_visualizer.generate_activity_visualization(messages)

        # Find peak hour
        hourly = viz.hourly_activity
        peak_hour = max(hourly.items(), key=lambda x: x[1])[0]
        period = f"{peak_hour:02d}:00-{(peak_hour + 1) % 24:02d}:00"

        return GroupStatistics(
            message_count=len(messages),
            total_characters=total_chars,
            participant_count=len(participants),
            most_active_period=period,
            emoji_count=emoji_stats.total_emoji_count,
            emoji_statistics=emoji_stats,
            activity_visualization=viz,
        )

    def _get_mock_data(self):
        """生成 Mock 数据用于调试渲染"""
        return {
            "topics": [
                {
                    "topic": "Bot 开发调试",
                    "contributors": ["开发者", "测试员"],
                    "detail": "大家讨论了如何为 Group Daily Analysis 插件添加 Debug 模式，以方便测试渲染效果而不消耗 Token。",
                },
                {
                    "topic": "中午吃什么",
                    "contributors": ["吃货A", "饿人B"],
                    "detail": "围绕中午点外卖还是去食堂进行了激烈的讨论，最终决定去吃黄焖鸡米饭。",
                },
            ],
            "user_titles": [
                {
                    "name": "开发者",
                    "qq": 10001,
                    "title": "Debug 大师",
                    "reason": "写了 100 行代码没有 Bug",
                    "personality": "群里的代码洁癖患者，不管聊什么话题都能绕回技术，发言简短但信息密度极高。遇到 Bug 的第一反应是打开 IDE，遇到聚餐的第一反应是问有没有包厢带电源。",
                },
                {
                    "name": "吃货A",
                    "qq": 10002,
                    "title": "干饭王",
                    "reason": "三句话不离吃饭",
                    "personality": "群里的美食雷达，每天上午10点准时开始讨论午饭，下午3点开始纠结晚饭。发言风格热情奔放，对食物的描述有一种让人血糖飙升的感染力。",
                },
            ],
            "golden_quotes": [
                {
                    "content": "程序和人有一个能跑就行。",
                    "sender": "运维小哥",
                    "reason": "道出了 IT 行业的真谛",
                },
                {
                    "content": "Bug 也是一种 Feature。",
                    "sender": "产品经理",
                    "reason": "重新定义了软件工程",
                },
            ],
        }

    def _generate_mock_statistics(self) -> GroupStatistics:
        """生成 Mock 统计数据"""
        emoji_stats = EmojiStatistics()
        emoji_stats.face_count = 66

        # Mock Visualization - 生成24小时的活跃度数据
        mock_msgs = []
        base_ts = int(datetime.now().timestamp())
        # 从23小时前到现在，生成消息
        for i in range(24):
            # 白天时段(9-22点)消息更多
            hour_offset = 23 - i  # 从23小时前开始
            count = 10 if 9 <= i <= 22 else 1
            for _ in range(count):
                mock_msgs.append(
                    {
                        "time": base_ts - (hour_offset * 3600),
                        "sender": {"user_id": 123},
                        "message": [],
                    }
                )

        viz = self.activity_visualizer.generate_activity_visualization(mock_msgs)

        return GroupStatistics(
            message_count=len(mock_msgs),
            total_characters=2333,
            participant_count=5,
            most_active_period="12:00-13:00",
            emoji_count=66,
            emoji_statistics=emoji_stats,
            activity_visualization=viz,
        )

    def _extract_text_messages(self, messages: list) -> list[dict]:
        text_msgs = []
        bot_ids = [str(i) for i in plugin_config.bot_qq_ids]

        for msg in messages:
            sender = msg.get("sender", {})
            user_id = str(sender.get("user_id", ""))

            if user_id in bot_ids:
                continue

            nickname = sender.get("card") or sender.get("nickname") or "群友"
            ts = msg.get("time", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%H:%M")

            content_parts = []
            for seg in msg.get("message", []):
                if seg["type"] == "text":
                    content_parts.append(seg["data"]["text"])
                elif seg["type"] == "at":
                    content_parts.append(f"@{seg['data'].get('qq', '')}")

            content = "".join(content_parts).strip()
            # 简单过滤
            if len(content) > 1 and not content.startswith("/"):
                # 清理一些特殊字符
                content = content.replace('"', "'").replace("\n", " ")
                text_msgs.append(
                    {"time": time_str, "sender": nickname, "content": content}
                )
        return text_msgs
