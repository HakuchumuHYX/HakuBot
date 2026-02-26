import json
import asyncio
import re
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from typing import Callable, TypeVar, Any
from nonebot.log import logger
from plugins.plugin_manager.enable import is_feature_enabled

from ..config import plugin_config
from ..models import (
    AnalysisResult, GroupStatistics, SummaryTopic, 
    UserTitle, GoldenQuote, TokenUsage, EmojiStatistics
)
from ..visualization.charts import ActivityVisualizer
from ..utils.llm import call_chat_completion, fix_json, _is_retryable_error
from .user_analyzer import UserAnalyzer

T = TypeVar('T')


def safe_prompt_format(prompt: str, **kwargs) -> str:
    """
    安全替换 prompt 中的少量占位符。

    说明：
    - 避免使用 str.format()，因为 prompt 里常包含 JSON 示例，带大量 `{}` 会触发 KeyError。
    - 这里只替换我们明确允许的变量：如 {messages_text} / {users_text} / {max_topics} 等。
    """
    for k, v in kwargs.items():
        prompt = prompt.replace("{" + k + "}", str(v))
    return prompt


class MessageAnalyzer:
    def __init__(self):
        self.activity_visualizer = ActivityVisualizer()
        self.user_analyzer = UserAnalyzer()

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
                is_retryable = _is_retryable_error(e) or isinstance(e, (json.JSONDecodeError, KeyError, TypeError, ValueError))
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
            tb = "".join(traceback.format_exception(type(last_error), last_error, last_error.__traceback__))
            logger.error(f"子任务[{name}] 在 {max_retries} 次尝试后仍失败: {last_error}\n{tb}")
        return [], TokenUsage()

    async def analyze_messages(self, messages: list, group_id: str, debug_mode: bool = False) -> AnalysisResult:
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

        # 自适应数量计算：基于有效文本消息数量
        msg_count = len(text_messages)
        dynamic_topics = min(10, plugin_config.max_topics + msg_count // 500)
        dynamic_quotes = min(10, plugin_config.max_golden_quotes + msg_count // 500)
        dynamic_titles = min(15, plugin_config.max_user_titles + msg_count // 500)
        
        logger.info(f"自适应数量计算：话题 {dynamic_topics}，称号 {dynamic_titles}，金句 {dynamic_quotes} (有效消息数: {msg_count})")

        # 3. LLM 分析 — 每个子任务独立重试，三者并发执行
        subtasks = []
        subtask_names: list[str | None] = []  # 用于最后的完整性检查
        
        # 话题分析
        if plugin_config.topic_analysis_enabled and is_feature_enabled("group_daily_analysis", "topics", group_id, "0"):
            async def topics_single(text):
                return await self._analyze_topics_single(text, dynamic_topics)
            async def topics_merge(items):
                return await self._merge_topics(items, dynamic_topics)
                
            subtasks.append(self._run_subtask_with_retry(
                "话题分析",
                lambda: self._analyze_with_strategy(
                    text_messages, 
                    topics_single, 
                    topics_merge
                ),
            ))
            subtask_names.append("topics")
        else:
            subtasks.append(asyncio.sleep(0, result=([], TokenUsage())))
            subtask_names.append(None)

        # 用户称号
        # 注意：用户称号分析需要原始消息(raw messages)来做 user_id 统计；
        # text_messages 仅用于拼接 prompt 文本。
        if plugin_config.user_title_analysis_enabled and is_feature_enabled("group_daily_analysis", "user_titles", group_id, "0"):
            subtasks.append(self._run_subtask_with_retry(
                "用户称号",
                lambda: self._analyze_user_titles_safe(messages, text_messages, dynamic_titles),
            ))
            subtask_names.append("user_titles")
        else:
            subtasks.append(asyncio.sleep(0, result=([], TokenUsage())))
            subtask_names.append(None)

        # 金句分析
        if plugin_config.golden_quote_analysis_enabled and is_feature_enabled("group_daily_analysis", "golden_quotes", group_id, "0"):
            async def quotes_single(text):
                return await self._analyze_golden_quotes_single(text, dynamic_quotes)
            async def quotes_merge(items):
                return await self._merge_golden_quotes(items, dynamic_quotes)
                
            subtasks.append(self._run_subtask_with_retry(
                "金句分析",
                lambda: self._analyze_with_strategy(
                    text_messages,
                    quotes_single,
                    quotes_merge
                ),
            ))
            subtask_names.append("golden_quotes")
        else:
            subtasks.append(asyncio.sleep(0, result=([], TokenUsage())))
            subtask_names.append(None)

        results = await asyncio.gather(*subtasks, return_exceptions=True)

        # 解包结果
        topics = []
        user_titles = []
        golden_quotes = []

        topic_usage = TokenUsage()
        user_title_usage = TokenUsage()
        golden_quote_usage = TokenUsage()

        # 0: Topics
        if isinstance(results[0], tuple):
            topics, topic_usage = results[0]
        elif isinstance(results[0], Exception):
            logger.error(f"话题分析子任务异常: {results[0]}")

        # 1: User Titles
        if isinstance(results[1], tuple):
            user_titles, user_title_usage = results[1]
        elif isinstance(results[1], Exception):
            logger.error(f"用户称号子任务异常: {results[1]}")

        # 2: Golden Quotes
        if isinstance(results[2], tuple):
            golden_quotes, golden_quote_usage = results[2]
        elif isinstance(results[2], Exception):
            logger.error(f"金句分析子任务异常: {results[2]}")

        # 完整性检查：打印哪些已开启的分析项仍为空
        missing_parts = []
        items_map = {"topics": topics, "user_titles": user_titles, "golden_quotes": golden_quotes}
        for sname in subtask_names:
            if sname and not items_map.get(sname):
                missing_parts.append(sname)
        if missing_parts:
            logger.warning(f"以下分析项在重试后仍为空: {missing_parts}")

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
            map_tasks.append(self._run_chunk_with_retry(
                single_func, text, chunk_index=i, max_retries=chunk_retry_count
            ))

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

        logger.info(f"Map 阶段完成: {success_count}/{len(chunks)} 分片成功, {fail_count} 失败, 收集到 {len(flattened)} 条结果")

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

    async def _run_chunk_with_retry(
        self, 
        func: Callable[[str], Any], 
        text: str, 
        chunk_index: int,
        max_retries: int = 2,
    ) -> tuple[list, TokenUsage]:
        """
        带重试的分片执行
        
        Args:
            func: 分析函数
            text: 文本内容
            chunk_index: 分片索引（用于日志）
            max_retries: 最大重试次数
        
        Returns:
            (结果列表, TokenUsage)
        """
        last_error: Exception | None = None
        
        for attempt in range(max_retries):
            try:
                result = await func(text)
                if attempt > 0:
                    logger.info(f"分片 {chunk_index} 在第 {attempt + 1} 次尝试后成功")
                return result
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = 2.0 * (attempt + 1)
                    logger.warning(f"分片 {chunk_index} 失败 ({e})，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})...")
                    await asyncio.sleep(delay)
        
        # 所有重试都失败
        logger.error(f"分片 {chunk_index} 在 {max_retries} 次尝试后仍失败: {last_error}")
        return [], TokenUsage()

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

    async def _analyze_topics_single(self, messages_text: str, max_topics: int) -> tuple[list[SummaryTopic], TokenUsage]:
        prompt = safe_prompt_format(
            plugin_config.topic_analysis_prompt,
            max_topics=max_topics,
            messages_text=messages_text,
        )
        # 不再 catch 异常 - 由上层 _run_subtask_with_retry / _run_chunk_with_retry 负责重试
        content, tokens = await call_chat_completion(
            [{"role": "user", "content": prompt}], 
            temperature=0.1
        )
        json_str = fix_json(content)
        data = json.loads(json_str)
        return [SummaryTopic(**item) for item in data], tokens

    async def _analyze_golden_quotes_single(self, messages_text: str, max_golden_quotes: int) -> tuple[list[GoldenQuote], TokenUsage]:
        prompt = safe_prompt_format(
            plugin_config.golden_quote_analysis_prompt,
            max_golden_quotes=max_golden_quotes,
            messages_text=messages_text,
        )
        # 不再 catch 异常 - 由上层 _run_subtask_with_retry / _run_chunk_with_retry 负责重试
        content, tokens = await call_chat_completion(
            [{"role": "user", "content": prompt}],
            temperature=1.1
        )
        json_str = fix_json(content)
        data = json.loads(json_str)
        return [GoldenQuote(**item) for item in data], tokens

    async def _analyze_user_titles_safe(self, raw_messages: list, text_messages: list, max_titles: int) -> tuple[list[UserTitle], TokenUsage]:
        """
        用户称号分析（安全版）

        raw_messages: 原始 OneBot 消息结构，用于 user_id/活跃度统计
        text_messages: 已提取的文本消息，用于拼 prompt（time/sender/content）
        """
        # 优化2：集成用户活跃度分析
        # 1) 用 raw_messages 统计活跃用户（这里才能拿到 user_id）
        user_analysis = self.user_analyzer.analyze_users(raw_messages)
        top_users = self.user_analyzer.get_top_users(
            user_analysis, limit=max_titles
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

        base_prompt = safe_prompt_format(
            plugin_config.user_title_analysis_prompt, 
            users_text=text,
            max_user_titles=max_titles
        )

        prompt = f"""以下是群聊中最活跃的用户（按消息数量排序）。请**优先**为这些用户生成称号，并尽量在输出中包含 qq（若模板要求输出 qq 字段）：

{top_users_info}

{base_prompt}
"""

        # 不再 catch 异常 - 由上层 _run_subtask_with_retry 负责重试
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

    def _norm_key(self, s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).lower()

    def _local_merge_topics(self, topics: list[SummaryTopic]) -> list[SummaryTopic]:
        """
        本地合并话题（Reduce 失败时兜底，不依赖 LLM，尽量不丢数据）
        - key: 归一化后的 topic
        - contributors: 合并去重
        - detail: 取信息量更大的版本（更长者）
        - rank: 出现频次 + detail 长度 + 参与者数量
        """
        buckets: dict[str, dict] = {}
        for t in topics or []:
            try:
                topic_name = (t.topic or "").strip()
                key = self._norm_key(topic_name)
                if not key:
                    continue
                b = buckets.get(key)
                if not b:
                    b = {
                        "topic": topic_name,
                        "contributors": set(),
                        "detail": (t.detail or "").strip(),
                        "count": 0,
                    }
                    buckets[key] = b

                b["count"] += 1
                for c in (t.contributors or []):
                    if c and str(c).strip():
                        b["contributors"].add(str(c).strip())

                detail = (t.detail or "").strip()
                if len(detail) > len(b["detail"]):
                    b["detail"] = detail
            except Exception:
                continue

        merged: list[SummaryTopic] = []
        for b in buckets.values():
            merged.append(
                SummaryTopic(
                    topic=b["topic"],
                    contributors=sorted(list(b["contributors"]))[:5],
                    detail=b["detail"] or "",
                )
            )

        def _score(x: SummaryTopic) -> tuple[int, int, int]:
            key = self._norm_key(x.topic)
            cnt = int(buckets.get(key, {}).get("count", 1))
            return (cnt, len(x.detail or ""), len(x.contributors or []))

        merged.sort(key=_score, reverse=True)
        return merged

    def _local_merge_quotes(self, quotes: list[GoldenQuote]) -> list[GoldenQuote]:
        """
        本地合并金句（Reduce 失败时兜底，不依赖 LLM，尽量不丢数据）
        - key: 归一化后的 content
        - sender: 取出现次数最多的 sender
        - reason: 合并去重（用分号拼接）
        - rank: 出现频次 + reason 信息量
        """
        buckets: dict[str, dict] = {}
        for q in quotes or []:
            try:
                content = (q.content or "").strip()
                key = self._norm_key(content)
                if not key:
                    continue
                b = buckets.get(key)
                if not b:
                    b = {
                        "content": content,
                        "senders": Counter(),
                        "reasons": set(),
                        "count": 0,
                    }
                    buckets[key] = b

                b["count"] += 1
                sender = (q.sender or "").strip()
                if sender:
                    b["senders"][sender] += 1
                reason = (q.reason or "").strip()
                if reason:
                    b["reasons"].add(reason)
            except Exception:
                continue

        merged: list[GoldenQuote] = []
        for b in buckets.values():
            sender = b["senders"].most_common(1)[0][0] if b["senders"] else "群友"
            reason = "；".join(sorted(b["reasons"], key=len, reverse=True)[:3])
            merged.append(GoldenQuote(content=b["content"], sender=sender, reason=reason))

        def _score(x: GoldenQuote) -> tuple[int, int]:
            key = self._norm_key(x.content)
            cnt = int(buckets.get(key, {}).get("count", 1))
            return (cnt, len(x.reason or ""))

        merged.sort(key=_score, reverse=True)
        return merged

    # --- Mergers (Reduce) ---

    async def _merge_topics(self, topics: list[SummaryTopic], max_topics: int) -> tuple[list[SummaryTopic], TokenUsage]:
        if not topics:
            return [], TokenUsage()

        # Reduce 也可能输入非常多，先做一次本地预合并，避免 prompt 爆长导致输出截断/跑偏
        topics_for_llm = topics
        if len(topics_for_llm) > 60:
            topics_for_llm = self._local_merge_topics(topics_for_llm)[:60]

        topics_text = json.dumps([t.dict() for t in topics_for_llm], ensure_ascii=False, indent=2)

        prompt = safe_prompt_format(
            plugin_config.topic_merge_prompt,
            max_topics=max_topics,
            topics_text=topics_text,
        )

        strict_tail = (
            "\n\n---\n\n"
            "## 重要：只允许输出纯 JSON 数组\n"
            "1. 不要输出任何解释性文字\n"
            "2. 不要输出 markdown 代码块（```）\n"
            "3. 必须返回一个 JSON 数组，每个元素字段为：topic / contributors / detail\n"
        )

        total_usage = TokenUsage()
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                _prompt = prompt if attempt == 0 else (prompt + strict_tail)
                content, usage = await call_chat_completion(
                    [{"role": "user", "content": _prompt}],
                    temperature=0.1 if attempt == 0 else 0.0,
                )
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens

                json_str = fix_json(content)
                data = json.loads(json_str)
                if not isinstance(data, list):
                    raise ValueError("Reduce 返回 JSON 不是数组")
                merged = [SummaryTopic(**item) for item in data]
                return merged[: max_topics], total_usage
            except Exception as e:
                last_exc = e

        # 仍失败：本地兜底合并（不丢数据）
        logger.warning(f"话题合并(Reduce)降级处理，改用本地合并结果: {last_exc}")
        merged_local = self._local_merge_topics(topics)[: max_topics]
        return merged_local, total_usage

    async def _merge_golden_quotes(self, quotes: list[GoldenQuote], max_golden_quotes: int) -> tuple[list[GoldenQuote], TokenUsage]:
        if not quotes:
            return [], TokenUsage()

        # Reduce 也可能输入很多候选，先本地预合并用于缩短 prompt（不影响最终兜底：兜底仍用全量 quotes）
        quotes_for_llm = quotes
        if len(quotes_for_llm) > 80:
            quotes_for_llm = self._local_merge_quotes(quotes_for_llm)[:80]

        quotes_text = json.dumps([q.dict() for q in quotes_for_llm], ensure_ascii=False, indent=2)

        prompt = safe_prompt_format(
            plugin_config.golden_quote_merge_prompt,
            max_golden_quotes=max_golden_quotes,
            quotes_text=quotes_text,
        )

        strict_tail = (
            "\n\n---\n\n"
            "## 重要：只允许输出纯 JSON 数组\n"
            "1. 不要输出任何解释性文字\n"
            "2. 不要输出 markdown 代码块（```）\n"
            "3. 必须返回一个 JSON 数组，每个元素字段为：content / sender / reason\n"
        )

        total_usage = TokenUsage()
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                _prompt = prompt if attempt == 0 else (prompt + strict_tail)
                content, usage = await call_chat_completion(
                    [{"role": "user", "content": _prompt}],
                    temperature=0.1 if attempt == 0 else 0.0,
                )
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens

                json_str = fix_json(content)
                data = json.loads(json_str)
                if not isinstance(data, list):
                    raise ValueError("Reduce 返回 JSON 不是数组")
                merged = [GoldenQuote(**item) for item in data]
                return merged[: max_golden_quotes], total_usage
            except Exception as e:
                last_exc = e

        # 仍失败：本地兜底合并（关键：不再 quotes[:N]，避免丢后续分片数据）
        logger.warning(f"金句合并(Reduce)降级处理，改用本地合并结果: {last_exc}")
        merged_local = self._local_merge_quotes(quotes)[: max_golden_quotes]
        return merged_local, total_usage

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
