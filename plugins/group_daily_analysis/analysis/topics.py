import json
import asyncio
from collections import Counter
from typing import Callable, Any
from nonebot.log import logger
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import SummaryTopic, GoldenQuote, TokenUsage
from plugins.group_daily_analysis.utils.llm import call_chat_completion
from utils.llm.client import is_retryable_llm_error
from plugins.group_daily_analysis.analysis.schemas import (
    TopicsPayload,
    GoldenQuotesPayload,
)
from plugins.group_daily_analysis.analysis.analyzers.common import parse_payload_items

from plugins.group_daily_analysis.analysis.prompts import safe_prompt_format


class TopicsAnalysis:
    async def _analyze_topics_and_quotes_quality_with_strategy(
        self,
        messages: list,
        max_topics: int,
        max_golden_quotes: int,
        *,
        topics_enabled: bool,
        quotes_enabled: bool,
    ) -> tuple[tuple[list[SummaryTopic], list[GoldenQuote]], TokenUsage]:
        async def topics_single(text):
            return await self._analyze_topics_single(text, max_topics)

        async def topics_merge(items):
            return await self._merge_topics(items, max_topics)

        async def quotes_single(text):
            return await self._analyze_golden_quotes_single(text, max_golden_quotes)

        async def quotes_merge(items):
            return await self._merge_golden_quotes(items, max_golden_quotes)

        topics: list[SummaryTopic] = []
        quotes: list[GoldenQuote] = []
        topic_usage = TokenUsage()
        quote_usage = TokenUsage()
        if topics_enabled:
            try:
                topics, topic_usage = await self._analyze_with_strategy(
                    messages, topics_single, topics_merge
                )
            except Exception:
                logger.exception("话题分析失败，保留其他分析结果并使用本地降级")
        if quotes_enabled:
            try:
                quotes, quote_usage = await self._analyze_with_strategy(
                    messages, quotes_single, quotes_merge
                )
            except Exception:
                logger.exception("金句分析失败，保留其他分析结果并使用本地降级")

        total_usage = TokenUsage(
            prompt_tokens=topic_usage.prompt_tokens + quote_usage.prompt_tokens,
            completion_tokens=topic_usage.completion_tokens
            + quote_usage.completion_tokens,
            total_tokens=topic_usage.total_tokens + quote_usage.total_tokens,
        )
        return (topics, quotes), total_usage

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
                if is_retryable_llm_error(e):
                    raise
                last_error = e
                if attempt < max_retries - 1:
                    delay = 2.0 * (attempt + 1)
                    logger.warning(
                        f"分片 {chunk_index} 失败 ({e})，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})..."
                    )
                    await asyncio.sleep(delay)

        # 所有重试都失败
        logger.error(
            f"分片 {chunk_index} 在 {max_retries} 次尝试后仍失败: {last_error}"
        )
        return [], TokenUsage()

    def _build_ds_cached_messages(
        self, messages_text: str, task_prompt: str
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是群聊日报分析助手。请严格基于用户提供的群聊记录分析，"
                    "不要编造群友、发言或不存在的结论。输出必须是 JSON。"
                ),
            },
            {
                "role": "user",
                "content": f"以下是今日群聊记录：\n\n{messages_text}",
            },
            {
                "role": "user",
                "content": task_prompt,
            },
        ]

    async def _analyze_topics_single(
        self, messages_text: str, max_topics: int
    ) -> tuple[list[SummaryTopic], TokenUsage]:
        prompt = safe_prompt_format(
            plugin_config.topic_analysis_prompt,
            max_topics=max_topics,
            messages_text="聊天记录已在本次 API 请求的前一条 user message 中提供，请基于该消息中的完整记录分析。",
        )
        prompt += self._json_object_tail(
            '{"items":[{"topic":"话题名称","contributors":["用户1"],"detail":"描述"}]}'
        )
        # 请求层处理网络重试，分片层处理输出解析重试。
        content, tokens = await call_chat_completion(
            self._build_ds_cached_messages(messages_text, prompt),
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        data = parse_payload_items(content, TopicsPayload, module_name="话题分析")
        return [SummaryTopic(**item.model_dump()) for item in data[:max_topics]], tokens

    async def _analyze_golden_quotes_single(
        self, messages_text: str, max_golden_quotes: int
    ) -> tuple[list[GoldenQuote], TokenUsage]:
        prompt = safe_prompt_format(
            plugin_config.golden_quote_analysis_prompt,
            max_golden_quotes=max_golden_quotes,
            messages_text="聊天记录已在本次 API 请求的前一条 user message 中提供，请基于该消息中的完整记录筛选。",
        )
        prompt += self._json_object_tail(
            '{"items":[{"content":"金句原文","sender":"发言人","reason":"辣评"}]}'
        )
        # 请求层处理网络重试，分片层处理输出解析重试。
        content, tokens = await call_chat_completion(
            self._build_ds_cached_messages(messages_text, prompt),
            temperature=1.1,
            response_format={"type": "json_object"},
        )
        data = parse_payload_items(content, GoldenQuotesPayload, module_name="金句分析")
        return [
            GoldenQuote(**item.model_dump()) for item in data[:max_golden_quotes]
        ], tokens

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
                for c in t.contributors or []:
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
            merged.append(
                GoldenQuote(content=b["content"], sender=sender, reason=reason)
            )

        def _score(x: GoldenQuote) -> tuple[int, int]:
            key = self._norm_key(x.content)
            cnt = int(buckets.get(key, {}).get("count", 1))
            return (cnt, len(x.reason or ""))

        merged.sort(key=_score, reverse=True)
        return merged

    async def _merge_topics(
        self, topics: list[SummaryTopic], max_topics: int
    ) -> tuple[list[SummaryTopic], TokenUsage]:
        if not topics:
            return [], TokenUsage()

        # Reduce 也可能输入非常多，先做一次本地预合并，避免 prompt 爆长导致输出截断/跑偏
        topics_for_llm = topics
        if len(topics_for_llm) > 60:
            topics_for_llm = self._local_merge_topics(topics_for_llm)[:60]

        topics_text = json.dumps(
            [t.dict() for t in topics_for_llm], ensure_ascii=False, indent=2
        )

        prompt = safe_prompt_format(
            plugin_config.topic_merge_prompt,
            max_topics=max_topics,
            topics_text=topics_text,
        )

        strict_tail = self._json_object_tail(
            '{"items":[{"topic":"话题名称","contributors":["用户1"],"detail":"具体描述"}]}'
        )

        total_usage = TokenUsage()
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                _prompt = prompt if attempt == 0 else (prompt + strict_tail)
                content, usage = await call_chat_completion(
                    [{"role": "user", "content": _prompt}],
                    temperature=0.25 if attempt == 0 else 0.1,
                    response_format={"type": "json_object"},
                )
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens

                data = parse_payload_items(
                    content, TopicsPayload, module_name="话题合并"
                )
                merged = [SummaryTopic(**item.model_dump()) for item in data]
                return merged[:max_topics], total_usage
            except Exception as e:
                last_exc = e

        # 仍失败：本地兜底合并（不丢数据）
        logger.warning(f"话题合并(Reduce)降级处理，改用本地合并结果: {last_exc}")
        merged_local = self._local_merge_topics(topics)[:max_topics]
        return merged_local, total_usage

    async def _merge_golden_quotes(
        self, quotes: list[GoldenQuote], max_golden_quotes: int
    ) -> tuple[list[GoldenQuote], TokenUsage]:
        if not quotes:
            return [], TokenUsage()

        # Reduce 也可能输入很多候选，先本地预合并用于缩短 prompt（不影响最终兜底：兜底仍用全量 quotes）
        quotes_for_llm = quotes
        if len(quotes_for_llm) > 80:
            quotes_for_llm = self._local_merge_quotes(quotes_for_llm)[:80]

        quotes_text = json.dumps(
            [q.dict() for q in quotes_for_llm], ensure_ascii=False, indent=2
        )

        prompt = safe_prompt_format(
            plugin_config.golden_quote_merge_prompt,
            max_golden_quotes=max_golden_quotes,
            quotes_text=quotes_text,
        )

        strict_tail = self._json_object_tail(
            '{"items":[{"content":"金句原文","sender":"发言人","reason":"辣评"}]}'
        )

        total_usage = TokenUsage()
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                _prompt = prompt if attempt == 0 else (prompt + strict_tail)
                content, usage = await call_chat_completion(
                    [{"role": "user", "content": _prompt}],
                    temperature=0.8 if attempt == 0 else 0.3,
                    response_format={"type": "json_object"},
                )
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens

                data = parse_payload_items(
                    content, GoldenQuotesPayload, module_name="金句合并"
                )
                merged = [GoldenQuote(**item.model_dump()) for item in data]
                return merged[:max_golden_quotes], total_usage
            except Exception as e:
                last_exc = e

        # 仍失败：本地兜底合并（关键：不再 quotes[:N]，避免丢后续分片数据）
        logger.warning(f"金句合并(Reduce)降级处理，改用本地合并结果: {last_exc}")
        merged_local = self._local_merge_quotes(quotes)[:max_golden_quotes]
        return merged_local, total_usage
