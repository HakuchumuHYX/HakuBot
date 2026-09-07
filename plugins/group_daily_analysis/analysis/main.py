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
from plugins.group_daily_analysis.analysis.statistics import StatisticsAnalysis
from plugins.group_daily_analysis.analysis.topics import TopicsAnalysis
from plugins.group_daily_analysis.analysis.titles import TitlesAnalysis


class MessageAnalyzer(StatisticsAnalysis, TopicsAnalysis, TitlesAnalysis):
    def __init__(self):
        self.activity_visualizer = ActivityVisualizer()

    async def _run_subtask_with_retry(
        self,
        name: str,
        coro_factory: Callable[[], Any],
        max_retries: int = 3,
        base_delay: float = 3.0,
    ) -> tuple[list, TokenUsage]:
        """
        对单个分析子任务进行独立重试包装

        只有网络类/可重试异常才触发重试，JSON 解析等逻辑错误直接降级返回空。

        Args:
            name: 子任务名称（用于日志）
            coro_factory: 一个无参 callable，每次调用返回一个新的 coroutine
            max_retries: 最大重试次数
            base_delay: 基础退避延迟(秒)，实际延迟 = base_delay * attempt
        """
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                result = await coro_factory()
                if attempt > 0:
                    logger.info(f"子任务[{name}] 在第 {attempt + 1} 次尝试后成功")
                return result
            except Exception as e:
                last_error = e
                # JSON 解析等数据结构错误，现在也视为可重试的异常
                is_retryable = False  # Individual requests/chunks own retries; completed work is not replayed.
                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (attempt + 1)
                    logger.warning(
                        f"子任务[{name}] 失败 ({type(e).__name__}: {e})，"
                        f"{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(delay)
                    continue
                # 不可重试的异常，或已耗尽重试次数
                break

        # 所有重试用尽或遇到不可重试异常
        if last_error:
            tb = "".join(
                traceback.format_exception(
                    type(last_error), last_error, last_error.__traceback__
                )
            )
            logger.error(
                f"子任务[{name}] 在 {max_retries} 次尝试后仍失败: {last_error}\n{tb}"
            )
        return [], TokenUsage()

    async def analyze_messages(
        self, messages: list, group_id: str, debug_mode: bool = False
    ) -> AnalysisResult:
        """主分析流程（带子任务独立重试）"""
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
                golden_quotes=[GoldenQuote(**q) for q in mock_data["golden_quotes"]],
            )

        # 2. 准备 Prompt 上下文
        transcript_context = build_transcript_context(
            messages,
            bot_ids=[str(i) for i in plugin_config.bot_qq_ids],
        )
        text_messages = [
            {"time": msg.time, "sender": msg.sender, "content": msg.content}
            for msg in transcript_context.text_messages
        ]
        if not text_messages:
            logger.warning("没有有效的文本消息用于分析")
            return AnalysisResult(statistics=stats, topics=[], user_titles=[])

        # 自适应数量计算：基于有效文本消息数量
        msg_count = len(text_messages)
        dynamic_topics = min(10, plugin_config.max_topics + msg_count // 500)
        dynamic_quotes = min(10, plugin_config.max_golden_quotes + msg_count // 500)
        dynamic_titles = min(15, plugin_config.max_user_titles + msg_count // 500)

        logger.info(
            f"自适应数量计算：话题 {dynamic_topics}，称号 {dynamic_titles}，金句 {dynamic_quotes} (有效消息数: {msg_count})"
        )

        # 3. LLM 分析 — 话题+金句合并为单一子任务（节省约50% Map阶段API调用），与用户称号并发执行
        topics_enabled = plugin_config.topic_analysis_enabled and is_feature_enabled(
            "group_daily_analysis", "topics", group_id, "0"
        )
        quotes_enabled = (
            plugin_config.golden_quote_analysis_enabled
            and is_feature_enabled(
                "group_daily_analysis", "golden_quotes", group_id, "0"
            )
        )
        titles_enabled = (
            plugin_config.user_title_analysis_enabled
            and is_feature_enabled("group_daily_analysis", "user_titles", group_id, "0")
        )

        subtasks = []

        # 质量优先：话题和金句独立分析，但在同一子任务中顺序执行以保留 DeepSeek 前缀缓存机会
        if topics_enabled or quotes_enabled:
            subtasks.append(
                self._run_subtask_with_retry(
                    "话题+金句质量分析",
                    lambda: self._analyze_topics_and_quotes_quality_with_strategy(
                        text_messages, dynamic_topics, dynamic_quotes
                    ),
                )
            )
        else:
            subtasks.append(asyncio.sleep(0, result=(([], []), TokenUsage())))

        # 用户称号（独立子任务，不共享 chat log 前缀，保持并发）
        if titles_enabled:
            subtasks.append(
                self._run_subtask_with_retry(
                    "用户称号",
                    lambda: self._analyze_user_titles_safe(
                        transcript_context, dynamic_titles
                    ),
                )
            )
        else:
            subtasks.append(asyncio.sleep(0, result=([], TokenUsage())))

        results = await asyncio.gather(*subtasks, return_exceptions=True)

        # 解包结果
        topics: list = []
        user_titles: list = []
        golden_quotes: list = []

        combined_usage = TokenUsage()
        user_title_usage = TokenUsage()

        # 0: 联合分析结果 → (topics_list, quotes_list)
        if isinstance(results[0], tuple):
            (topics, golden_quotes), combined_usage = results[0]
        elif isinstance(results[0], Exception):
            logger.error(f"话题+金句联合分析子任务异常: {results[0]}")

        # 1: User Titles
        if isinstance(results[1], tuple):
            user_titles, user_title_usage = results[1]
        elif isinstance(results[1], Exception):
            logger.error(f"用户称号子任务异常: {results[1]}")

        if not user_titles and titles_enabled:
            fallback_titles = build_user_title_fallback(
                transcript_context, dynamic_titles
            )
            user_titles = [UserTitle(**item) for item in fallback_titles]
            if user_titles:
                logger.warning(
                    f"用户称号分析为空，已使用本地统计兜底生成 {len(user_titles)} 个称号"
                )

        # 剥离出独立 topic/quote 用于后续降级检查
        topic_usage = combined_usage
        golden_quote_usage = TokenUsage()

        if not topics and topics_enabled:
            fallback_topics = build_topic_fallback(transcript_context, dynamic_topics)
            topics = [SummaryTopic(**item) for item in fallback_topics]
            if topics:
                logger.warning(
                    f"话题分析为空，已使用本地统计兜底生成 {len(topics)} 个话题"
                )

        if not golden_quotes and quotes_enabled:
            fallback_quotes = build_golden_quote_fallback(
                transcript_context, dynamic_quotes
            )
            golden_quotes = [GoldenQuote(**item) for item in fallback_quotes]
            if golden_quotes:
                logger.warning(
                    f"金句分析为空，已使用本地候选兜底生成 {len(golden_quotes)} 条金句"
                )

        # 完整性检查
        missing_parts = []
        if topics_enabled and not topics:
            missing_parts.append("topics")
        if titles_enabled and not user_titles:
            missing_parts.append("user_titles")
        if quotes_enabled and not golden_quotes:
            missing_parts.append("golden_quotes")
        if missing_parts:
            logger.warning(f"以下分析项在重试后仍为空: {missing_parts}")

        # 汇总 TokenUsage
        stats.token_usage = TokenUsage(
            prompt_tokens=combined_usage.prompt_tokens + user_title_usage.prompt_tokens,
            completion_tokens=combined_usage.completion_tokens
            + user_title_usage.completion_tokens,
            total_tokens=combined_usage.total_tokens + user_title_usage.total_tokens,
        )

        # 记录详细的 Token 使用情况
        logger.info(
            "Token 使用统计 - "
            f"质量分析(话题+金句): {combined_usage.total_tokens} (P:{combined_usage.prompt_tokens}/C:{combined_usage.completion_tokens}), "
            f"称号: {user_title_usage.total_tokens} (P:{user_title_usage.prompt_tokens}/C:{user_title_usage.completion_tokens}), "
            f"总计: {stats.token_usage.total_tokens} (P:{stats.token_usage.prompt_tokens}/C:{stats.token_usage.completion_tokens})"
        )

        return AnalysisResult(
            statistics=stats,
            topics=topics,
            user_titles=user_titles,
            golden_quotes=golden_quotes,
        )

    async def _analyze_with_strategy(
        self,
        messages: list,
        single_func: Callable[[str], Any],
        merge_func: Callable[[list], Any] | None = None,
        chunk_retry_count: int = 2,
    ):
        """
        通用分析策略：自动选择直接分析或 Map-Reduce

        Args:
            messages: 消息列表
            single_func: 单次分析函数
            merge_func: 合并函数（可选）
            chunk_retry_count: 单个分片失败时的重试次数
        """
        total_len = sum(len(m["content"]) for m in messages)

        # Direct Mode（也带重试，避免网络波动导致分析缺失）
        if total_len <= plugin_config.max_input_length:
            text = self._msgs_to_text(messages)
            return await self._run_chunk_with_retry(
                single_func, text, chunk_index=0, max_retries=chunk_retry_count
            )

        # Map-Reduce Mode
        logger.info(f"消息长度 ({total_len}) 超过阈值，启用 Map-Reduce 分段分析...")
        chunks = self._split_messages(messages, plugin_config.max_input_length)

        # Map Phase: 并发处理所有分片
        map_tasks = []
        for i, chunk in enumerate(chunks):
            text = self._msgs_to_text(chunk)
            map_tasks.append(
                self._run_chunk_with_retry(
                    single_func, text, chunk_index=i, max_retries=chunk_retry_count
                )
            )

        # Map Results
        results = await asyncio.gather(*map_tasks, return_exceptions=True)

        # Flatten and Accumulate Tokens
        flattened = []
        total_usage = TokenUsage()
        success_count = 0
        fail_count = 0

        for i, res in enumerate(results):
            if isinstance(res, tuple):
                data, usage = res
                if data:  # 有有效数据
                    flattened.extend(data)
                    success_count += 1
                if isinstance(usage, TokenUsage):
                    total_usage.prompt_tokens += usage.prompt_tokens
                    total_usage.completion_tokens += usage.completion_tokens
                    total_usage.total_tokens += usage.total_tokens
            elif isinstance(res, Exception):
                logger.warning(f"Map 分片 {i} 最终失败: {res}")
                fail_count += 1
            else:
                # Fallback if someone returns just list (shouldn't happen with updated code)
                if isinstance(res, list) and res:
                    flattened.extend(res)
                    success_count += 1

        logger.info(
            f"Map 阶段完成: {success_count}/{len(chunks)} 分片成功, {fail_count} 失败, 收集到 {len(flattened)} 条结果"
        )

        # 如果所有分片都失败了，返回空结果
        if not flattened:
            logger.warning("所有 Map 分片都失败，无法生成分析结果")
            return [], total_usage

        # Reduce Phase
        if merge_func and flattened:
            logger.info(f"开始 Reduce 阶段，合并 {len(flattened)} 条结果...")
            try:
                data, usage = await merge_func(flattened)
                if isinstance(usage, TokenUsage):
                    total_usage.prompt_tokens += usage.prompt_tokens
                    total_usage.completion_tokens += usage.completion_tokens
                    total_usage.total_tokens += usage.total_tokens
                return data, total_usage
            except Exception as e:
                logger.warning(f"Reduce 阶段失败，返回未合并的 Map 结果: {e}")
                # Reduce 失败时，返回截断的未合并结果作为降级
                return flattened, total_usage

        return flattened, total_usage

    async def _analyze_combined_with_strategy(
        self,
        messages: list,
        max_topics: int,
        max_golden_quotes: int,
        chunk_retry_count: int = 2,
    ) -> tuple[tuple[list, list], TokenUsage]:
        """
        话题+金句联合分析策略，每个 chunk 只发一次请求。

        Returns:
            ((topics_list, quotes_list), TokenUsage)
        """
        total_len = sum(len(m["content"]) for m in messages)

        # Direct Mode
        if total_len <= plugin_config.max_input_length:
            text = self._msgs_to_text(messages)
            result, usage = await self._run_chunk_with_retry(
                lambda t: self._analyze_topics_and_quotes_single(
                    t, max_topics, max_golden_quotes
                ),
                text,
                chunk_index=0,
                max_retries=chunk_retry_count,
            )
            topics, quotes = result if result else ([], [])
            return (topics, quotes), usage

        # Map-Reduce Mode
        logger.info(f"消息长度 ({total_len}) 超过阈值，启用 Map-Reduce 联合分段分析...")
        chunks = self._split_messages(messages, plugin_config.max_input_length)

        map_tasks = [
            self._run_chunk_with_retry(
                lambda t, i=i: self._analyze_topics_and_quotes_single(
                    t, max_topics, max_golden_quotes
                ),
                self._msgs_to_text(chunk),
                chunk_index=i,
                max_retries=chunk_retry_count,
            )
            for i, chunk in enumerate(chunks)
        ]
        results = await asyncio.gather(*map_tasks, return_exceptions=True)

        all_topics: list = []
        all_quotes: list = []
        total_usage = TokenUsage()
        success_count = 0

        for i, res in enumerate(results):
            if isinstance(res, tuple):
                (chunk_topics, chunk_quotes), usage = res
                if chunk_topics or chunk_quotes:
                    all_topics.extend(chunk_topics or [])
                    all_quotes.extend(chunk_quotes or [])
                    success_count += 1
                if isinstance(usage, TokenUsage):
                    total_usage.prompt_tokens += usage.prompt_tokens
                    total_usage.completion_tokens += usage.completion_tokens
                    total_usage.total_tokens += usage.total_tokens
            elif isinstance(res, Exception):
                logger.warning(f"联合分析 Map 分片 {i} 最终失败: {res}")

        logger.info(
            f"联合 Map 阶段完成: {success_count}/{len(chunks)} 分片成功, 收集话题 {len(all_topics)} 条, 金句 {len(all_quotes)} 条"
        )

        if not all_topics and not all_quotes:
            return ([], []), total_usage

        # Reduce Phase（话题和金句分别 merge）
        final_topics = all_topics
        final_quotes = all_quotes

        if all_topics:
            try:
                merged_topics, merge_usage = await self._merge_topics(
                    all_topics, max_topics
                )
                final_topics = merged_topics
                total_usage.prompt_tokens += merge_usage.prompt_tokens
                total_usage.completion_tokens += merge_usage.completion_tokens
                total_usage.total_tokens += merge_usage.total_tokens
            except Exception as e:
                logger.warning(f"话题 Reduce 失败，使用未合并结果: {e}")

        if all_quotes:
            try:
                merged_quotes, merge_usage = await self._merge_golden_quotes(
                    all_quotes, max_golden_quotes
                )
                final_quotes = merged_quotes
                total_usage.prompt_tokens += merge_usage.prompt_tokens
                total_usage.completion_tokens += merge_usage.completion_tokens
                total_usage.total_tokens += merge_usage.total_tokens
            except Exception as e:
                logger.warning(f"金句 Reduce 失败，使用未合并结果: {e}")

        return (final_topics, final_quotes), total_usage

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

    def _json_object_tail(self, schema_example: str) -> str:
        return (
            "\n\n---\n\n"
            "## 最高优先级输出格式要求\n"
            "由于本次调用启用了 DeepSeek JSON Output，最终回复必须是一个 JSON object。"
            "这条要求覆盖上方模板里任何“返回 JSON 数组”的旧示例。\n"
            "不要输出 markdown，不要输出解释，不要输出顶层数组。\n"
            f"唯一允许的顶层格式示例：{schema_example}\n"
            "其中 items 必须是数组。"
        )

    def _norm_key(self, s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).lower()
