import json
import asyncio
import re
from datetime import datetime
from nonebot.log import logger

from ..config import plugin_config
from ..models import (
    AnalysisResult, GroupStatistics, SummaryTopic, 
    UserTitle, GoldenQuote, TokenUsage, EmojiStatistics
)
from ..visualization.charts import ActivityVisualizer
from ..utils.llm import call_chat_completion, fix_json

class MessageAnalyzer:
    def __init__(self):
        self.activity_visualizer = ActivityVisualizer()

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
            tasks.append(asyncio.sleep(0, result=[]))

        # 用户称号 (通常不需要严格的 Map-Reduce，若过长则简单截断或采样，这里暂时使用简单策略：过长则只取最后 N 条)
        if plugin_config.user_title_analysis_enabled:
            # 对于 User Title，我们不进行 Merge，因为那是基于全局感知的。
            # 如果太长，我们截断为最近的 max_input_length * 1.5 字符
            # 这是一个权衡
            tasks.append(self._analyze_user_titles_safe(text_messages))
        else:
            tasks.append(asyncio.sleep(0, result=[]))

        # 金句分析
        if plugin_config.golden_quote_analysis_enabled:
            tasks.append(self._analyze_with_strategy(
                text_messages,
                self._analyze_golden_quotes_single,
                self._merge_golden_quotes
            ))
        else:
            tasks.append(asyncio.sleep(0, result=[]))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        topics = results[0] if isinstance(results[0], list) else []
        user_titles = results[1] if isinstance(results[1], list) else []
        golden_quotes = results[2] if isinstance(results[2], list) else []
        
        stats.golden_quotes = golden_quotes
        
        return AnalysisResult(
            statistics=stats,
            topics=topics,
            user_titles=user_titles
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
        
        # Flatten
        flattened = []
        for res in results:
            if isinstance(res, list):
                flattened.extend(res)
            elif isinstance(res, Exception):
                logger.warning(f"Map 任务失败: {res}")
        
        # Reduce
        if merge_func and flattened:
            logger.info(f"Map 阶段完成，合并 {len(flattened)} 条结果...")
            return await merge_func(flattened)
        
        return flattened

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

    async def _analyze_topics_single(self, messages_text: str) -> list[SummaryTopic]:
        prompt = plugin_config.topic_analysis_prompt.format(
            max_topics=plugin_config.max_topics,
            messages_text=messages_text
        )
        try:
            content, _ = await call_chat_completion([{"role": "user", "content": prompt}])
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [SummaryTopic(**item) for item in data]
        except Exception as e:
            logger.error(f"话题分析(Single)失败: {e}")
            return []

    async def _analyze_golden_quotes_single(self, messages_text: str) -> list[GoldenQuote]:
        prompt = plugin_config.golden_quote_analysis_prompt.format(
            max_golden_quotes=plugin_config.max_golden_quotes,
            messages_text=messages_text
        )
        try:
            content, _ = await call_chat_completion([{"role": "user", "content": prompt}])
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [GoldenQuote(**item) for item in data]
        except Exception as e:
            logger.error(f"金句分析(Single)失败: {e}")
            return []

    async def _analyze_user_titles_safe(self, messages: list) -> list[UserTitle]:
        # 简单的截断策略：只保留最后 8000 字符 (稍微放宽一点限制，给用户画像多点上下文)
        limit = int(plugin_config.max_input_length * 1.5)
        
        # 从后往前取
        selected_msgs = []
        current_len = 0
        for msg in reversed(messages):
            if current_len + len(msg["content"]) > limit:
                break
            selected_msgs.append(msg)
            current_len += len(msg["content"])
        
        selected_msgs.reverse() # 恢复时间顺序
        text = self._msgs_to_text(selected_msgs)
        
        prompt = plugin_config.user_title_analysis_prompt.format(
            users_text=text
        )
        try:
            content, _ = await call_chat_completion([{"role": "user", "content": prompt}])
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [UserTitle(**item) for item in data]
        except Exception as e:
            logger.error(f"用户称号分析失败: {e}")
            return []

    # --- Mergers (Reduce) ---

    async def _merge_topics(self, topics: list[SummaryTopic]) -> list[SummaryTopic]:
        if not topics:
            return []
        
        # 将对象转为简化文本供 LLM 合并
        topics_text = json.dumps([t.dict() for t in topics], ensure_ascii=False, indent=2)
        
        prompt = plugin_config.topic_merge_prompt.format(
            max_topics=plugin_config.max_topics,
            topics_text=topics_text
        )
        try:
            content, _ = await call_chat_completion([{"role": "user", "content": prompt}])
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [SummaryTopic(**item) for item in data]
        except Exception as e:
            logger.error(f"话题合并(Reduce)失败: {e}")
            # 降级：直接返回前 N 个，或者按某种规则排序
            return topics[:plugin_config.max_topics]

    async def _merge_golden_quotes(self, quotes: list[GoldenQuote]) -> list[GoldenQuote]:
        if not quotes:
            return []
            
        quotes_text = json.dumps([q.dict() for q in quotes], ensure_ascii=False, indent=2)
        
        prompt = plugin_config.golden_quote_merge_prompt.format(
            max_golden_quotes=plugin_config.max_golden_quotes,
            quotes_text=quotes_text
        )
        try:
            content, _ = await call_chat_completion([{"role": "user", "content": prompt}])
            json_str = fix_json(content)
            data = json.loads(json_str)
            return [GoldenQuote(**item) for item in data]
        except Exception as e:
            logger.error(f"金句合并(Reduce)失败: {e}")
            return quotes[:plugin_config.max_golden_quotes]

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
                    emoji_stats.total_emoji_count += 1
                    emoji_stats.face_count += 1
                # More emoji types can be added here
        
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
        emoji_stats.total_emoji_count = 66
        emoji_stats.face_count = 66
        
        # Mock Visualization (Empty or Basic)
        # 这里为了简化，我们尽量让 ActivityVisualizer 处理空数据或伪造数据
        # 但由于 ActivityVisualizer 可能依赖 matplotlib，直接生成可能有难度
        # 我们这里尝试传入一些假消息给 visualizer
        mock_msgs = []
        base_ts = int(datetime.now().timestamp())
        for i in range(24):
            # 伪造每小时的消息
            count = 10 if 9 <= i <= 22 else 1
            for _ in range(count):
                mock_msgs.append({
                    "time": base_ts - i * 3600, 
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
