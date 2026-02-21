from __future__ import annotations

import asyncio
import re
from typing import Optional

from nonebot.log import logger

from ...search_service import format_search_results, tavily_search
from ..types import MusicServiceConfig
from .models import Plan


class WebSearchMixin:
    """
    Tavily 联网检索 + 结果质量控制/排序/站点倾向。

    依赖：
      - self.cfg: MusicServiceConfig
      - self._log / self._summarize_web_results: LoggingMixin
      - self._llm_expand_web_queries: LLMPlanMixin
    """

    cfg: MusicServiceConfig  # for type checkers

    # --- domain bias presets (扩展站点列表) ---
    _DOMAINS_MEME = [
        "bilibili.com", "moegirl.org.cn", "zhihu.com", "baike.baidu.com", 
        "wikipedia.org", "douban.com", "tieba.baidu.com", "weibo.com",
        "nga.cn", "acfun.cn",
    ]
    _DOMAINS_MUSIC = [
        "music.163.com", "y.qq.com", "zhihu.com", "baike.baidu.com", 
        "wikipedia.org", "douban.com", "kugou.com", "kuwo.cn",
        "xiami.com", "spotify.com", "apple.com/music",
    ]
    _DOMAINS_GAME = [
        "bilibili.com", "zhihu.com", "baike.baidu.com", "wikipedia.org",
        "nga.cn", "gamersky.com", "3dmgame.com", "gamepedia.com",
        "fandom.com", "steam.com",
    ]
    _DOMAINS_ANIME = [
        "bilibili.com", "moegirl.org.cn", "zhihu.com", "baike.baidu.com", 
        "wikipedia.org", "douban.com", "bgm.tv", "acfun.cn",
        "anidb.net", "myanimelist.net",
    ]
    _DOMAINS_VTUBER = [
        "bilibili.com", "moegirl.org.cn", "zhihu.com", "youtube.com",
        "hololive.tv", "nijisanji.jp", "twitter.com", "x.com",
    ]

    # --- quality keywords (扩展关键词列表) ---
    _QUALITY_KW = [
        # 歌曲相关
        "BGM", "bgm", "原曲", "原唱", "配乐", "OST", "ost",
        "歌名", "歌曲", "歌词", "音译", "翻唱", "cover",
        "主题曲", "片头曲", "片尾曲", "插曲", "ED", "OP",
        # 来源相关
        "出处", "梗", "口癖", "台词", "片段", "名场面",
        # 艺术家相关
        "歌手", "演唱", "作曲", "作词", "编曲",
        # 外语相关
        "日文", "日语", "原名", "英文", "罗马音", "假名",
        # 平台相关
        "网易云", "QQ音乐", "Spotify", "Apple Music",
    ]
    
    # --- 高相关性关键词（权重更高）---
    _HIGH_QUALITY_KW = [
        "原曲", "是什么歌", "歌名", "BGM", "OST", "配乐", "歌词",
    ]

    def _pick_domain_bias(self, plan: Plan) -> Optional[list[str]]:
        if not self.cfg.web_search_domain_bias_enabled:
            return None
        style = (plan.context_style or "").strip().lower()

        # domain_hint 优先（如果 LLM 给了）
        hint = (plan.domain_hint or "").strip().lower()
        if hint == "bilibili":
            return ["bilibili.com"]
        if hint == "zhihu":
            return ["zhihu.com"]
        if hint == "moegirl":
            return ["moegirl.org.cn"]
        if hint == "wiki":
            return ["wikipedia.org"]

        if style == "meme":
            return self._DOMAINS_MEME
        if style == "anime":
            return self._DOMAINS_ANIME
        if style == "game":
            return self._DOMAINS_GAME
        if style == "music":
            return self._DOMAINS_MUSIC
        return None

    def _is_search_good(self, results: list[dict]) -> bool:
        if not results:
            return False
        blob = " ".join(
            [
                (r.get("title") or "") + " " + (r.get("content") or "")
                for r in results[:5]
                if isinstance(r, dict)
            ]
        )
        if not blob.strip():
            return False
        hit = 0
        for kw in self._QUALITY_KW:
            if kw in blob:
                hit += 1
        return hit >= 2

    @staticmethod
    def _web_tokenize(text: str | None) -> list[str]:
        if not text:
            return []
        parts = re.findall(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]{2,}", text)
        out: list[str] = []
        for p in parts:
            p = p.strip()
            if p and p not in out:
                out.append(p)
        return out[:8]

    def _rank_web_results(self, results: list[dict], *, plan: Plan, raw_request: str = "") -> list[dict]:
        """
        把"像歌曲页/包含原名信息/匹配用户原始请求"的结果排到前面，
        避免 format_search_results 截断后只剩无关页面。
        
        改进点：
        1. 不仅匹配 plan.song_title/song_artist，还匹配用户原始请求中的关键词
        2. 匹配 alternative_queries 中的变体
        3. 增加对"原曲"、"是什么歌"等高价值信号的权重
        """
        title_tokens = self._web_tokenize(plan.song_title)
        artist_tokens = self._web_tokenize(plan.song_artist)
        
        # 从用户原始请求中提取关键词（重要！第一次LLM解析可能是错的）
        request_tokens = self._web_tokenize(raw_request)
        
        # 从 alternative_queries 中也提取 token
        alt_tokens: list[str] = []
        for alt in (plan.alternative_queries or []):
            alt_tokens.extend(self._web_tokenize(alt))

        songish_kws = [
            "歌名",
            "歌曲",
            "曲目",
            "原曲",
            "原唱",
            "歌词",
            "配乐",
            "ost",
            "bgm",
            "mv",
            "music video",
            "official",
            "日语",
            "日語",
            "原名",
            "ローマ字",
            "romanization",
        ]
        
        # 高价值信号关键词（表示这个结果很可能包含"正确答案"）
        high_value_kws = [
            "是什么歌", "原曲", "歌名", "BGM", "OST", "配乐",
            "ソング", "song", "楽曲", "曲目", "主题曲", "插曲",
        ]

        def _score(r: dict) -> int:
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip()
            text = f"{title}\n{content}".lower()
            text_original = f"{title}\n{content}"  # 保留原始大小写用于日语匹配

            score = 0
            
            # 1. 基础歌曲相关关键词
            for kw in songish_kws:
                if kw.lower() in text:
                    score += 2
            
            # 2. 高价值信号（更高权重）
            for kw in high_value_kws:
                if kw.lower() in text or kw in text_original:
                    score += 6
            
            # 3. 匹配 LLM 解析的 song_title/song_artist
            for tok in title_tokens:
                if tok.lower() in text:
                    score += 5
            for tok in artist_tokens:
                if tok.lower() in text:
                    score += 3
            
            # 4. 匹配用户原始请求中的关键词（重要！）
            for tok in request_tokens:
                if tok.lower() in text:
                    score += 4
            
            # 5. 匹配 alternative_queries 中的 token
            for tok in alt_tokens:
                if tok.lower() in text:
                    score += 3

            # 6. 轻惩罚人物百科类（防止压过歌曲页）
            if (
                ("萌娘百科" in title or "百科" in title)
                and ("曲目" not in content)
                and ("原創曲目" not in content)
                and ("原创曲目" not in content)
            ):
                score -= 2
            
            # 7. 惩罚明显无关的结果（非音乐相关网站）
            url = (r.get("url") or "").lower()
            irrelevant_signals = ["huggingface.co", "github.com/", ".csv", ".json", ".py"]
            for sig in irrelevant_signals:
                if sig in url:
                    score -= 10
            
            # 8. 惩罚通用性/目录类标题（这些页面通常不包含具体歌曲信息）
            generic_title_signals = [
                "目录", "合集", "大全", "汇总", "列表", "全曲",
                "交流", "体验", "讨论", "评测", "攻略",
                "原声音乐专辑", "原声带", "Soundtrack",
                "配乐合集", "音乐合集",
            ]
            title_lower = title.lower()
            for sig in generic_title_signals:
                if sig.lower() in title_lower:
                    score -= 5
            
            # 9. 额外奖励：content 中包含具体歌曲信息的结果
            # 如果 content 中同时包含歌名关键词和歌手/来源关键词，加分
            content_lower = content.lower()
            content_has_song_signal = any(
                kw.lower() in content_lower 
                for kw in ["歌名", "原曲", "BGM", "OST", "曲名", "歌曲名"]
            )
            content_has_artist_signal = any(
                kw.lower() in content_lower 
                for kw in ["演唱", "歌手", "作曲", "原唱", "翻唱"]
            )
            if content_has_song_signal and content_has_artist_signal:
                score += 5

            return score

        ranked = sorted([r for r in (results or []) if isinstance(r, dict)], key=_score, reverse=True)
        return ranked

    async def _multi_search(
        self,
        queries: list[str],
        *,
        include_domains: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        并行执行多个 Tavily 搜索查询，合并结果并去重。
        优化：使用 asyncio.gather 并行执行以提高速度。
        """
        # 按全局配置分配 max_results
        total_max = 5
        try:
            from ...config import plugin_config

            total_max = int(getattr(plugin_config, "web_search_max_results", 5) or 5)
        except Exception:
            total_max = 5
        if total_max < 1:
            total_max = 5

        per_query = max(1, int((total_max + len(queries) - 1) / max(1, len(queries))))

        self._log(
            "tavily.multi_search.plan",
            {
                "queries": queries,
                "include_domains": include_domains,
                "total_max": total_max,
                "per_query": per_query,
                "parallel": True,
            },
        )

        # 定义单个查询的异步函数
        async def _search_one(q: str) -> tuple[str, list[dict]]:
            try:
                rs = await tavily_search(q, max_results=per_query, include_domains=include_domains)
                return q, rs
            except Exception as e:
                logger.warning(f"[ai_assistant.music] Tavily 搜索失败: query={q!r} err={e}")
                self._log("tavily.search.error", {"query": q, "error": str(e)})
                return q, []

        # 并行执行所有查询
        tasks = [_search_one(q) for q in queries]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并结果并去重
        merged: list[dict] = []
        seen: set[str] = set()

        for result in results_list:
            if isinstance(result, Exception):
                continue
            q, rs = result
            
            if rs:
                # 全量日志：Tavily 原始返回
                self._log(
                    "tavily.search.raw",
                    {
                        "query": q,
                        "include_domains": include_domains,
                        "count": len(rs),
                        "results": rs,
                    },
                )

                # 精简日志：title/url/snippet
                self._log(
                    "tavily.search.response",
                    {"query": q, "count": len(rs), "results": self._summarize_web_results(rs)},
                )

            for item in rs:
                url = (item.get("url") or "").strip()
                key = url or (item.get("title") or "") + (item.get("content") or "")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)

                if len(merged) >= total_max:
                    break
            if len(merged) >= total_max:
                break

        return merged

    async def _web_search_slow_path(self, raw_request: str, plan: Plan) -> str | None:
        """
        站点倾向 + 质量检测 + 重试
        返回 format_search_results 文本（用于喂给第二次 _llm_plan）。
        """
        qlist = plan.web_queries or [raw_request[:60]]

        include_domains = self._pick_domain_bias(plan) if self.cfg.web_search_domain_bias_enabled else None

        # 1) 先带 domain bias 搜
        results = await self._multi_search(qlist, include_domains=include_domains)
        self._log(
            "web_search.initial",
            {
                "raw_request": raw_request,
                "queries": qlist,
                "include_domains": include_domains,
                "is_good": self._is_search_good(results),
                "count": len(results),
                "results": self._summarize_web_results(results),
            },
        )

        # 2) 如果带 bias 搜不到或质量很差，放宽域名再来一次（不计入 retry_times）
        if (not results) or (not self._is_search_good(results)):
            if include_domains:
                relaxed = await self._multi_search(qlist, include_domains=None)
                self._log(
                    "web_search.relaxed",
                    {
                        "raw_request": raw_request,
                        "queries": qlist,
                        "include_domains": None,
                        "is_good": self._is_search_good(relaxed),
                        "count": len(relaxed),
                        "results": self._summarize_web_results(relaxed),
                    },
                )
                # 如果放宽更好，替换
                if self._is_search_good(relaxed) or (len(relaxed) > len(results)):
                    results = relaxed

        # 3) D：质量仍差，则按 retry_times 做“query 扩展 + 再搜”
        retries = max(0, int(getattr(self.cfg, "web_search_retry_times", 1)))
        attempt = 0
        while attempt < retries and not self._is_search_good(results):
            attempt += 1
            reason = "results are low quality (not enough BGM/song/source signals)"
            qlist2 = await self._llm_expand_web_queries(raw_request, plan=plan, reason=reason)

            self._log(
                "web_search.retry",
                {
                    "attempt": attempt,
                    "retries": retries,
                    "reason": reason,
                    "expanded_queries": qlist2,
                },
            )

            # 扩展查询：优先不加域名限制，避免越限越偏
            results2 = await self._multi_search(qlist2, include_domains=None)
            self._log(
                "web_search.retry_results",
                {
                    "attempt": attempt,
                    "count": len(results2),
                    "is_good": self._is_search_good(results2),
                    "results": self._summarize_web_results(results2),
                },
            )

            # 合并并判断是否变好
            combined = results[:]  # copy
            seen2 = set((r.get("url") or "") + (r.get("title") or "") for r in combined)
            for r in results2:
                k = (r.get("url") or "") + (r.get("title") or "")
                if k in seen2:
                    continue
                combined.append(r)
                seen2.add(k)
            # 取更好的结果集合（优先质量，其次数量）
            if self._is_search_good(combined) or (len(combined) > len(results)):
                results = combined

        if not results:
            self._log("web_search.final", {"raw_request": raw_request, "result": None})
            return None

        # 把“像歌曲页/含原名信息”的结果排到前面
        results = self._rank_web_results(results, plan=plan, raw_request=raw_request)
        self._log(
            "web_search.ranked",
            {
                "raw_request": raw_request,
                "count": len(results),
                "results": self._summarize_web_results(results),
            },
        )

        formatted = format_search_results(results, max_chars=2000)
        self._log(
            "web_search.final",
            {
                "raw_request": raw_request,
                "is_good": self._is_search_good(results),
                "count": len(results),
                "formatted_context": formatted,
            },
        )
        return formatted
