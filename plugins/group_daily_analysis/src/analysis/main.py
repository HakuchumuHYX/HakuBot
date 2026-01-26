import json
import asyncio
import re
import traceback
from datetime import datetime
from nonebot.log import logger

from ..config import plugin_config
from ..models import (
    AnalysisResult, GroupStatistics, SummaryTopic, 
    UserTitle, GoldenQuote, TokenUsage, EmojiStatistics
)
from ..visualization.charts import ActivityVisualizer
from ..utils.llm import call_chat_completion, fix_json
from .user_analyzer import UserAnalyzer

class MessageAnalyzer:
    def __init__(self):
        self.activity_visualizer = ActivityVisualizer()
        self.user_analyzer = UserAnalyzer()

    async def analyze_messages(self, messages: list, group_id: str, debug_mode: bool = False) -> AnalysisResult:
        """主分析流程"""
        # 0. Debug 模式下的统计数据处理
        if debug_mode and not messages:
            stats = self._generate_mock_statistics()
        else:
            stats = self._calculate_statistics(messages)
            
        # Debug 模式下如果统计数据为空（计算失败），强制使用 Mock
        if debug_mode and stats.message_count == 0:
            stats = self._generate_mock_statistics()

        # 1. Debug 模式跳过 LLM
        if debug_mode:
            logger.info("Debug 模式：跳过 LLM 分析，使用 Mock 数据")
            mock_data = self._get_mock_data()
            return AnalysisResult(
                statistics=stats,
                topics=[SummaryTopic(**t) for t in mock_data["topics"]],
                user_titles=[UserTitle(**u) for u in mock_data["user_titles"]],
                golden_quotes=[GoldenQuote(**q) for q in mock_data["golden_quotes"]]
            )

        # 2. 准备 Prompt 上下文
        text_messages = self._extract_text_messages(messages)
        if not text_messages:
            logger.warning("没有有效的文本消息用于分析")
            return AnalysisResult(
                statistics=stats,
                topics=[],
                user_titles=[]
            )

        # 3. LLM 分析 (Map-Reduce / Direct)
        tasks = []
        
        # 话题分析
        if plugin_config.topic_analysis_enabled:
            tasks.append(self._analyze_with_strategy(
                text_messages, 
                self._analyze_topics_single, 
                self._merge_topics
            ))
        else:
            tasks.append(asyncio.sleep(0, result=([], TokenUsage())))

        # 用户称号
        # 注意：用户称号分析需要原始消息(raw messages)来做 user_id 统计；
        # text_messages 仅用于拼接 prompt 文本。
        if plugin_config.user_title_analysis_enabled:
            tasks.append(self._analyze_user_titles_safe(messages, text_messages))
        else:
            tasks.append(asyncio.sleep(0, result=([], TokenUsage())))

        # 金句分析
        if plugin_config.golden_quote_analysis_enabled:
            tasks.append(self._analyze_with_strategy(
                text_messages,
                self._analyze_golden_quotes_single,
                self._merge_golden_quotes
            ))
        else:
            tasks.append(asyncio.sleep(0, result=([], TokenUsage())))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 把子任务异常显式打出来，避免“模块悄悄消失”
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                tb = "".join(traceback.format_exception(type(res), res, res.__traceback__))
                logger.error(f"分析子任务失败 idx={idx}: {res}\n{tb}")

        topics = []
        user_titles = []
        golden_quotes = []

        # 优化4：详细的 Token 统计（拆分 prompt / completion / total）
        topic_usage = TokenUsage()
        user_title_usage = TokenUsage()
        golden_quote_usage = TokenUsage()

        # Unpack results
        # 0: Topics
        if isinstance(results[0], tuple):
            topics, topic_usage = results[0]

        # 1: User Titles
        if isinstance(results[1], tuple):
            user_titles, user_title_usage = results[1]

        # 2: Golden Quotes
        if isinstance(results[2], tuple):
            golden_quotes, golden_quote_usage = results[2]

        # 汇总 TokenUsage
        stats.token_usage = TokenUsage(
            prompt_tokens=topic_usage.prompt_tokens + user_title_usage.prompt_tokens + golden_quote_usage.prompt_tokens,
            completion_tokens=topic_usage.completion_tokens + user_title_usage.completion_tokens + golden_quote_usage.completion_tokens,
            total_tokens=topic_usage.total_tokens + user_title_usage.total_tokens + golden_quote_usage.total_tokens,
        )

        # 记录详细的 Token 使用情况
        logger.info(
            "Token 使用统计 - "
            f"话题: {topic_usage.total_tokens} (P:{topic_usage.prompt_tokens}/C:{topic_usage.completion_tokens}), "
            f"称号: {user_title_usage.total_tokens} (P:{user_title_usage.prompt_tokens}/C:{user_title_usage.completion_tokens}), "
            f"金句: {golden_quote_usage.total_tokens} (P:{golden_quote_usage.prompt_tokens}/C:{golden_quote_usage.completion_tokens}), "
            f"总计: {stats.token_usage.total_tokens} (P:{stats.token_usage.prompt_tokens}/C:{stats.token_usage.completion_tokens})"
        )
        
        return AnalysisResult(
            statistics=stats,
            topics=topics,
            user_titles=user_titles,
            golden_quotes=golden_quotes
        )

    async def _analyze_with_strategy(self, messages: list, single_func, merge_func=None):
        """通用分析策略：自动选择直接分析或 Map-Reduce"""
        total_len = sum(len(m["content"]) for m in messages)

        # Direct Mode
        if total_len <= plugin_config.max_input_length:
            text = self._msgs_to_text(messages)
            return await single_func(text)

        # Map-Reduce Mode
        logger.info(f"消息长度 ({total_len}) 超过阈值，启用 Map-Reduce 分段分析...")
        chunks = self._split_messages(messages, plugin_config.max_input_length)

        map_tasks = []
        for chunk in chunks:
            text = self._msgs_to_text(chunk)
            map_tasks.append(single_func(text))

        # Map Results
        results = await asyncio.gather(*map_tasks, return_exceptions=True)

        # Flatten and Accumulate Tokens
        flattened = []
        total_usage = TokenUsage()

        for res in results:
            if isinstance(res, tuple):
                data, usage = res
                flattened.extend(data)
                if isinstance(usage, TokenUsage):
                    total_usage.prompt_tokens += usage.prompt_tokens
                    total_usage.completion_tokens += usage.completion_tokens
                    total_usage.total_tokens += usage.total_tokens
            elif isinstance(res, Exception):
                logger.warning(f"Map 任务失败: {res}")
            else:
                # Fallback if someone returns just list (shouldn't happen with updated code)
                if isinstance(res, list):
                    flattened.extend(res)

        # Reduce
        if merge_func and flattened:
            logger.info(f"Map 阶段完成，合并 {len(flattened)} 条结果...")
            data, usage = await merge_func(flattened)
            if isinstance(usage, TokenUsage):
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens
            return data, total_usage

        return flattened, total_usage

    def _split_messages(self, messages: list, chunk_size: int) -> list[list]:
        """按字符数切分消息块"""
        chunks = []
        current_chunk = []
        current_len = 0
        
        for msg in messages:
            msg_len = len(msg["content"])
            # 如果当前块加上这条消息会超限，且当前块不为空，则截断当前块
            if current_len + msg_len > chunk_size and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_len = 0
            
            current_chunk.append(msg)
            current_len += msg_len
            
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def _msgs_to_text(self, messages: list) -> str:
        return "\n".join(
            [f"[{msg['time']}] {msg['sender']}: {msg['content']}" for msg in messages]
        )

    # --- Single Analyzers (Map) ---

    async def _analyze_topics_single(self, messages_text: str) -> tuple[list[SummaryTopic], TokenUsage]:
        prompt = plugin_config.topic_analysis_prompt.format(
            max_topics=plugin_config.max_topics,
            messages_text=messages_text
        )
        try:
            # Topic analysis needs structure, low temp
            content, tokens = await call_chat_completion(
                [{"role": "user", "content": prompt}], 
                temperature=0.1
            )
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [SummaryTopic(**item) for item in data], tokens
        except Exception as e:
            logger.error(f"话题分析(Single)失败: {e}")
            return [], TokenUsage()

    async def _analyze_golden_quotes_single(self, messages_text: str) -> tuple[list[GoldenQuote], TokenUsage]:
        prompt = plugin_config.golden_quote_analysis_prompt.format(
            max_golden_quotes=plugin_config.max_golden_quotes,
            messages_text=messages_text
        )
        try:
            # Golden quotes need creativity, high temp
            content, tokens = await call_chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=1.1
            )
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [GoldenQuote(**item) for item in data], tokens
        except Exception as e:
            logger.error(f"金句分析(Single)失败: {e}")
            return [], TokenUsage()

    async def _analyze_user_titles_safe(self, raw_messages: list, text_messages: list) -> tuple[list[UserTitle], TokenUsage]:
        """
        用户称号分析（安全版）

        raw_messages: 原始 OneBot 消息结构，用于 user_id/活跃度统计
        text_messages: 已提取的文本消息，用于拼 prompt（time/sender/content）
        """
        # 优化2：集成用户活跃度分析
        # 1) 用 raw_messages 统计活跃用户（这里才能拿到 user_id）
        user_analysis = self.user_analyzer.analyze_users(raw_messages)
        top_users = self.user_analyzer.get_top_users(
            user_analysis, limit=plugin_config.max_user_titles
        )

        if not top_users:
            logger.warning("没有活跃用户，跳过称号分析")
            return [], TokenUsage()

        # 2) 使用 text_messages 拼 prompt 文本（避免把CQ码/段结构塞进模型）
        limit = int(plugin_config.max_input_length * 1.5)

        selected_msgs = []
        current_len = 0
        for msg in reversed(text_messages):
            if current_len + len(msg["content"]) > limit:
                break
            selected_msgs.append(msg)
            current_len += len(msg["content"])

        selected_msgs.reverse()
        text = self._msgs_to_text(selected_msgs)

        # 3) 构建包含 user_id 的活跃用户信息（提升 LLM 命中率）
        def _display_name(u: dict) -> str:
            return (u.get("card") or u.get("nickname") or "").strip() or "群友"

        top_users_info = "\n".join(
            [
                f"- {_display_name(u)} (qq: {u.get('user_id')}, 消息数: {u.get('message_count')})"
                for u in top_users
            ]
        )

        base_prompt = plugin_config.user_title_analysis_prompt.format(users_text=text)

        prompt = f"""以下是群聊中最活跃的用户（按消息数量排序）。请**优先**为这些用户生成称号，并尽量在输出中包含 qq（若模板要求输出 qq 字段）：

{top_users_info}

{base_prompt}
"""

        try:
            content, tokens = await call_chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            json_str = fix_json(content)
            data = json.loads(json_str)

            # 4) 尝试补齐 qq（如果 LLM 没给）
            user_titles: list[UserTitle] = []
            for item in data:
                name = item.get("name", "")
                qq = item.get("qq", 0)

                if not qq:
                    for u in top_users:
                        if name and name in {_display_name(u), u.get("nickname", ""), u.get("card", "")}:
                            uid = u.get("user_id", "")
                            qq = int(uid) if str(uid).isdigit() else 0
                            break

                user_titles.append(
                    UserTitle(
                        name=name,
                        qq=qq or None,
                        title=item.get("title", ""),
                        mbti=item.get("mbti", ""),
                        reason=item.get("reason", ""),
                    )
                )

            logger.info(f"用户称号分析完成，生成了 {len(user_titles)} 个称号")
            return user_titles, tokens
        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            logger.error(f"用户称号分析失败: {e}\n{tb}")
            return [], TokenUsage()

    # --- Mergers (Reduce) ---

    async def _merge_topics(self, topics: list[SummaryTopic]) -> tuple[list[SummaryTopic], TokenUsage]:
        if not topics:
            return [], TokenUsage()
        
        # 将对象转为简化文本供 LLM 合并
        topics_text = json.dumps([t.dict() for t in topics], ensure_ascii=False, indent=2)
        
        prompt = plugin_config.topic_merge_prompt.format(
            max_topics=plugin_config.max_topics,
            topics_text=topics_text
        )
        try:
            # Merging needs structure, low temp
            content, tokens = await call_chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=0.1
            )
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [SummaryTopic(**item) for item in data], tokens
        except Exception as e:
            logger.error(f"话题合并(Reduce)失败: {e}")
            # 降级：直接返回前 N 个
            return topics[:plugin_config.max_topics], TokenUsage()

    async def _merge_golden_quotes(self, quotes: list[GoldenQuote]) -> tuple[list[GoldenQuote], TokenUsage]:
        if not quotes:
            return [], TokenUsage()
            
        quotes_text = json.dumps([q.dict() for q in quotes], ensure_ascii=False, indent=2)
        
        prompt = plugin_config.golden_quote_merge_prompt.format(
            max_golden_quotes=plugin_config.max_golden_quotes,
            quotes_text=quotes_text
        )
        try:
            # Merging quotes still needs structure even if content is creative
            content, tokens = await call_chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=0.1
            )
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [GoldenQuote(**item) for item in data], tokens
        except Exception as e:
            logger.error(f"金句合并(Reduce)失败: {e}")
            return quotes[:plugin_config.max_golden_quotes], TokenUsage()

    # --- Helpers ---

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
                    emoji_stats.face_details[f"face_{face_id}"] = emoji_stats.face_details.get(f"face_{face_id}", 0) + 1
                elif seg["type"] == "mface":
                    # 动画表情/魔法表情
                    emoji_stats.mface_count += 1
                    emoji_id = seg["data"].get("emoji_id", "unknown")
                    emoji_stats.face_details[f"mface_{emoji_id}"] = emoji_stats.face_details.get(f"mface_{emoji_id}", 0) + 1
                elif seg["type"] == "bface":
                    # 超级表情
                    emoji_stats.bface_count += 1
                    emoji_id = seg["data"].get("p", "unknown")
                    emoji_stats.face_details[f"bface_{emoji_id}"] = emoji_stats.face_details.get(f"bface_{emoji_id}", 0) + 1
                elif seg["type"] == "sface":
                    # 小表情
                    emoji_stats.sface_count += 1
                    emoji_id = seg["data"].get("id", "unknown")
                    emoji_stats.face_details[f"sface_{emoji_id}"] = emoji_stats.face_details.get(f"sface_{emoji_id}", 0) + 1
                elif seg["type"] == "image":
                    # 检查是否是动画表情（通过summary字段判断）
                    data = seg.get("data", {})
                    summary = data.get("summary", "")
                    if "动画表情" in summary or "表情" in summary:
                        emoji_stats.mface_count += 1
                        file_name = data.get("file", "unknown")
                        emoji_stats.face_details[f"animated_{file_name}"] = emoji_stats.face_details.get(f"animated_{file_name}", 0) + 1
                elif seg["type"] in ["record", "video"] and "emoji" in str(seg.get("data", {})).lower():
                    # 其他可能的表情类型
                    emoji_stats.other_emoji_count += 1
        
        # Most active period & Visualization
        viz = self.activity_visualizer.generate_activity_visualization(messages)
        
        # Find peak hour
        hourly = viz.hourly_activity
        peak_hour = max(hourly.items(), key=lambda x: x[1])[0]
        period = f"{peak_hour:02d}:00-{(peak_hour+1)%24:02d}:00"

        return GroupStatistics(
            message_count=len(messages),
            total_characters=total_chars,
            participant_count=len(participants),
            most_active_period=period,
            emoji_count=emoji_stats.total_emoji_count,
            emoji_statistics=emoji_stats,
            activity_visualization=viz
        )

    def _get_mock_data(self):
        """生成 Mock 数据用于调试渲染"""
        return {
            "topics": [
                {
                    "topic": "Bot 开发调试",
                    "contributors": ["开发者", "测试员"],
                    "detail": "大家讨论了如何为 Group Daily Analysis 插件添加 Debug 模式，以方便测试渲染效果而不消耗 Token。"
                },
                {
                    "topic": "中午吃什么",
                    "contributors": ["吃货A", "饿人B"],
                    "detail": "围绕中午点外卖还是去食堂进行了激烈的讨论，最终决定去吃黄焖鸡米饭。"
                }
            ],
            "user_titles": [
                {
                    "name": "开发者",
                    "qq": 10001,
                    "title": "Debug 大师",
                    "mbti": "INTJ",
                    "reason": "写了 100 行代码没有 Bug"
                },
                {
                    "name": "吃货A",
                    "qq": 10002,
                    "title": "干饭王",
                    "mbti": "ESFP",
                    "reason": "三句话不离吃饭"
                }
            ],
            "golden_quotes": [
                {
                    "content": "程序和人有一个能跑就行。",
                    "sender": "运维小哥",
                    "reason": "道出了 IT 行业的真谛"
                },
                {
                    "content": "Bug 也是一种 Feature。",
                    "sender": "产品经理",
                    "reason": "重新定义了软件工程"
                }
            ]
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
                mock_msgs.append({
                    "time": base_ts - (hour_offset * 3600), 
                    "sender": {"user_id": 123},
                    "message": []
                })
        
        viz = self.activity_visualizer.generate_activity_visualization(mock_msgs)
        
        return GroupStatistics(
            message_count=len(mock_msgs),
            total_characters=2333,
            participant_count=5,
            most_active_period="12:00-13:00",
            emoji_count=66,
            emoji_statistics=emoji_stats,
            activity_visualization=viz
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
                text_msgs.append({
                    "time": time_str,
                    "sender": nickname,
                    "content": content
                })
        return text_msgs
