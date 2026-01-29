from __future__ import annotations

import random
import re
from typing import Optional

from ..core.model import Song
from ..core.platform import BaseMusicPlayer
from ..types import MusicServiceConfig, PickStrategy


# 尝试导入pypinyin用于拼音匹配
try:
    from pypinyin import lazy_pinyin, Style
    _HAS_PYPINYIN = True
except ImportError:
    _HAS_PYPINYIN = False


class SongSearchMixin:
    """
    歌曲检索与选歌逻辑（网易云固定平台 + 低质量结果检测 + 兜底搜索 + 多变体搜索）。

    依赖：
      - self.cfg: MusicServiceConfig
      - await self.ensure_inited()
      - self.get_player(...)
      - self._log / self._summarize_songs
      - self._llm_verify_relevance (from LLMPlanMixin)
    """

    cfg: MusicServiceConfig  # for type checkers

    @staticmethod
    def _get_pinyin_initials(text: str) -> str:
        """获取文本的拼音首字母"""
        if not _HAS_PYPINYIN or not text:
            return ""
        try:
            initials = lazy_pinyin(text, style=Style.FIRST_LETTER)
            return "".join(initials).lower()
        except Exception:
            return ""

    @staticmethod
    def _get_pinyin_full(text: str) -> str:
        """获取文本的完整拼音"""
        if not _HAS_PYPINYIN or not text:
            return ""
        try:
            pinyin_list = lazy_pinyin(text)
            return "".join(pinyin_list).lower()
        except Exception:
            return ""

    def _tokens(self, t: str | None) -> list[str]:
        """提取文本中的关键词token"""
        if not t:
            return []
        # 中/英/数/日文假名/常见字符（至少长度2），用于相关性判断
        parts = re.findall(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]{2,}", t)
        out: list[str] = []
        for p in parts:
            p = p.strip()
            if p and p not in out:
                out.append(p)
        return out[:8]

    def _relevance_score(
        self,
        song: Song,
        *,
        title_tokens: list[str],
        artist_tokens: list[str],
        raw_request: str = "",
    ) -> int:
        """
        计算歌曲与请求的相关性分数。
        增强：支持拼音首字母和全拼匹配。
        """
        name = (song.name or "").lower()
        artists = (song.artists or "").lower()
        score = 0

        # 基础token匹配
        for tok in title_tokens:
            t = tok.lower()
            if t and t in name:
                score += 3
        for tok in artist_tokens:
            t = tok.lower()
            if t and t in artists:
                score += 2

        # 拼音匹配（如果可用）
        if _HAS_PYPINYIN and raw_request:
            request_initials = self._get_pinyin_initials(raw_request)
            request_pinyin = self._get_pinyin_full(raw_request)
            name_initials = self._get_pinyin_initials(song.name or "")
            name_pinyin = self._get_pinyin_full(song.name or "")

            # 拼音首字母匹配
            if request_initials and name_initials:
                if request_initials in name_initials or name_initials in request_initials:
                    score += 2
            
            # 完整拼音匹配
            if request_pinyin and name_pinyin:
                if request_pinyin in name_pinyin or name_pinyin in request_pinyin:
                    score += 3

        return score

    def _best_score(self, songs: list[Song], *, title_tokens: list[str], artist_tokens: list[str], raw_request: str = "") -> int:
        """获取候选列表中的最高分"""
        if not songs:
            return 0
        if not title_tokens and not artist_tokens:
            return 999  # 不做相关性判断
        return max(
            (self._relevance_score(s, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request) for s in songs[:5]),
            default=0,
        )

    def _is_low_quality(self, songs: list[Song], *, title_tokens: list[str], artist_tokens: list[str], raw_request: str = "") -> bool:
        """
        判断搜索结果是否低质量（不相关）。
        
        修复：当 title_tokens 和 artist_tokens 都为空时，使用 raw_request 进行基础相关性检测。
        """
        if not songs:
            return True
        
        # 如果有token，用token匹配
        if title_tokens or artist_tokens:
            best = self._best_score(songs, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
            # 如果完全不命中（best=0），视为"搜歪"
            return best <= 0
        
        # 如果没有token但有raw_request，用raw_request做基础相关性检测
        if raw_request:
            # 从raw_request中提取关键词
            raw_tokens = self._tokens(raw_request)
            if raw_tokens:
                # 检查搜索结果中是否有任何歌曲与请求有关联
                for song in songs[:5]:
                    name = (song.name or "").lower()
                    artists = (song.artists or "").lower()
                    
                    for tok in raw_tokens:
                        t = tok.lower()
                        if t in name or t in artists:
                            # 找到匹配，认为不是低质量
                            return False
                    
                    # 拼音匹配
                    if _HAS_PYPINYIN:
                        request_initials = self._get_pinyin_initials(raw_request)
                        name_initials = self._get_pinyin_initials(song.name or "")
                        if request_initials and name_initials:
                            # 至少有3个字符的拼音重叠
                            common_len = 0
                            for i in range(min(len(request_initials), len(name_initials))):
                                if request_initials[i] == name_initials[i]:
                                    common_len += 1
                            if common_len >= 3:
                                return False
                
                # 没有找到任何匹配，视为低质量
                return True
        
        # 没有任何信息可用于判断，保守地返回False（让后续流程处理）
        return False

    async def search_songs(
        self,
        keyword: str,
        *,
        platform_hint: str | None = None,
        extra: str | None = None,
        song_title: str | None = None,
        song_artist: str | None = None,
        alternative_queries: list[str] | None = None,
        raw_request: str = "",
    ) -> tuple[BaseMusicPlayer, list[Song], bool] | tuple[None, list[Song], bool]:
        """
        搜索歌曲，支持多变体搜索和LLM相关性验证。
        
        新增参数：
        - alternative_queries: 备选搜索词列表
        - raw_request: 原始用户请求（用于相关性评分）
        """
        await self.ensure_inited()

        # 平台固定：默认走网易云 Web API（netease）；nodejs 仅作为兜底（可用但可能被限流）
        player = self.get_player(name="netease") or self.get_player(name="netease_nodejs")
        if not player:
            self._log(
                "music.search_songs.no_player",
                {"keyword": keyword, "forced": ["netease", "netease_nodejs"]},
            )
            return None, [], True

        if platform_hint is not None:
            self._log("music.search_songs.platform_hint.ignored", {"platform_hint": platform_hint})

        title_tokens = self._tokens(song_title)
        artist_tokens = self._tokens(song_artist)
        limit = max(1, min(int(self.cfg.candidate_limit), 50))

        # 1) 主查询（由 llm 已强制"歌名在前歌手在后"）
        songs = await player.fetch_songs(keyword=keyword, limit=limit, extra=extra)
        best_score = self._best_score(songs, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
        lowq = self._is_low_quality(songs, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
        
        self._log(
            "music.search_songs.result",
            {
                "keyword": keyword,
                "extra": extra,
                "song_title": song_title,
                "song_artist": song_artist,
                "title_tokens": title_tokens,
                "artist_tokens": artist_tokens,
                "forced_player": {"name": player.platform.name, "display_name": player.platform.display_name},
                "limit": limit,
                "count": len(songs),
                "best_score_top5": best_score,
                "low_quality": lowq,
                "songs": self._summarize_songs(songs, max_items=limit),
            },
        )

        final_songs = songs
        final_lowq = lowq

        # 2) 多变体搜索：如果主查询低质量且有备选查询词
        enable_alternatives = getattr(self.cfg, "enable_alternative_queries", True)
        if (not songs or lowq) and alternative_queries and enable_alternatives:
            self._log(
                "music.search_songs.trying_alternatives",
                {"alternative_queries": alternative_queries},
            )
            
            for alt_query in alternative_queries:
                if alt_query == keyword:
                    continue
                    
                alt_songs = await player.fetch_songs(keyword=alt_query, limit=limit, extra=extra)
                alt_score = self._best_score(alt_songs, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
                alt_lowq = self._is_low_quality(alt_songs, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
                
                self._log(
                    "music.search_songs.alternative_result",
                    {
                        "alternative_query": alt_query,
                        "count": len(alt_songs),
                        "best_score_top5": alt_score,
                        "low_quality": alt_lowq,
                        "songs": self._summarize_songs(alt_songs, max_items=3),
                    },
                )
                
                # 如果备选结果更好，使用备选结果
                if alt_songs and not alt_lowq:
                    final_songs = alt_songs
                    final_lowq = False
                    self._log(
                        "music.search_songs.alternative_accepted",
                        {"accepted_query": alt_query},
                    )
                    break
                elif alt_songs and (not songs or alt_score > best_score):
                    # 备选虽然也低质量，但比主查询好
                    final_songs = alt_songs
                    final_lowq = alt_lowq
                    best_score = alt_score

        # 3) 兜底（方案B）：仅在"无结果 or 明显不相关"时，再做一次搜索
        # 兜底 query 优先用"纯歌名"，否则用"纯歌手"
        if (not final_songs) or final_lowq:
            fallback_query: str | None = None
            if song_title:
                fallback_query = song_title
            elif song_artist:
                fallback_query = song_artist

            if fallback_query and fallback_query != keyword:
                songs2 = await player.fetch_songs(keyword=fallback_query, limit=limit, extra=extra)
                best_score2 = self._best_score(songs2, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
                lowq2 = self._is_low_quality(songs2, title_tokens=title_tokens, artist_tokens=artist_tokens, raw_request=raw_request)
                
                self._log(
                    "music.search_songs.fallback",
                    {
                        "from_keyword": keyword,
                        "fallback_keyword": fallback_query,
                        "forced_player": {"name": player.platform.name, "display_name": player.platform.display_name},
                        "count": len(songs2),
                        "best_score_top5": best_score2,
                        "low_quality": lowq2,
                        "songs": self._summarize_songs(songs2, max_items=limit),
                    },
                )
                
                if songs2 and (not lowq2):
                    final_songs = songs2
                    final_lowq = False
                elif songs2 and not final_songs:
                    # 如果兜底虽然 low_quality，但至少有结果，也优先返回兜底（比完全为空强）
                    final_songs = songs2
                    final_lowq = lowq2

        # 4) LLM相关性二次验证（如果仍然低质量且启用了LLM验证）
        enable_llm_check = getattr(self.cfg, "enable_llm_relevance_check", True)
        if final_songs and final_lowq and enable_llm_check and raw_request:
            self._log(
                "music.search_songs.llm_relevance_check",
                {"raw_request": raw_request, "candidate_count": len(final_songs)},
            )
            
            try:
                # 构造候选列表供LLM验证
                candidates = [
                    {"name": s.name or "", "artists": s.artists or ""}
                    for s in final_songs[:5]
                ]
                
                relevant, best_idx, reason = await self._llm_verify_relevance(raw_request, candidates)
                
                self._log(
                    "music.search_songs.llm_relevance_result",
                    {"relevant": relevant, "best_index": best_idx, "reason": reason},
                )
                
                if relevant:
                    # LLM认为相关，取消低质量标记
                    final_lowq = False
                    # 如果LLM指定了最佳候选，重排列表
                    if best_idx is not None and 1 <= best_idx <= len(final_songs):
                        best_song = final_songs[best_idx - 1]
                        final_songs = [best_song] + [s for i, s in enumerate(final_songs) if i != best_idx - 1]
                        self._log(
                            "music.search_songs.llm_reorder",
                            {"best_index": best_idx, "best_song": best_song.name},
                        )
            except Exception as e:
                self._log(
                    "music.search_songs.llm_relevance_error",
                    {"error": str(e)},
                )

        return player, final_songs, final_lowq

    def pick_song(
        self,
        songs: list[Song],
        *,
        strategy: PickStrategy,
        raw_request: str,
        song_title: str | None = None,
        song_artist: str | None = None,
    ) -> Song | None:
        """
        根据策略选择一首歌曲。
        
        优化：无论策略是什么，都优先匹配 song_title（LLM 解析出的歌名）。
        这样即使 strategy="first"，也会优先选择与歌名匹配的歌曲。
        """
        if not songs:
            return None

        # 优先匹配 song_title（如果提供）
        if song_title:
            title_lower = song_title.lower().strip()
            
            # 1. 精确匹配歌名
            for s in songs:
                name = (s.name or "").lower().strip()
                if name == title_lower:
                    return s
            
            # 2. 歌名包含（忽略大小写和空格）
            title_normalized = re.sub(r'\s+', '', title_lower)
            for s in songs:
                name = (s.name or "").lower()
                name_normalized = re.sub(r'\s+', '', name)
                if title_normalized == name_normalized:
                    return s
            
            # 3. 歌名部分匹配（歌名包含在结果中，或结果包含在歌名中）
            for s in songs:
                name = (s.name or "").lower()
                name_normalized = re.sub(r'\s+', '', name)
                if title_normalized in name_normalized or name_normalized in title_normalized:
                    return s
            
            # 4. 如果还提供了 song_artist，尝试同时匹配歌名和歌手
            if song_artist:
                artist_lower = song_artist.lower().strip()
                for s in songs:
                    name = (s.name or "").lower()
                    artists = (s.artists or "").lower()
                    # 歌名部分匹配 + 歌手部分匹配
                    if (title_lower in name or name in title_lower) and artist_lower in artists:
                        return s

        # 如果 song_title 匹配失败，按原策略选择
        if strategy == "first":
            return songs[0]
        
        if strategy == "best_match":
            text = raw_request.lower()
            scored: list[tuple[int, Song]] = []
            
            for s in songs:
                name = (s.name or "").lower()
                artists = (s.artists or "").lower()
                score = 0
                
                # 基础token匹配
                for token in set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text)):
                    if token in name:
                        score += 3
                    if token in artists:
                        score += 2
                
                # 拼音匹配加分
                if _HAS_PYPINYIN:
                    request_initials = self._get_pinyin_initials(raw_request)
                    name_initials = self._get_pinyin_initials(s.name or "")
                    if request_initials and name_initials:
                        if request_initials in name_initials or name_initials in request_initials:
                            score += 1
                
                scored.append((score, s))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1] if scored else songs[0]

        # random
        return random.choice(songs)
