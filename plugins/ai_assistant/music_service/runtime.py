from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as localstore

from ..service import call_chat_completion, format_search_results, tavily_search
from ..utils import remove_markdown
from .core import Downloader, MusicRenderer, MusicSender, Playlist, SendContext
from .core.model import Song
from .core.platform import BaseMusicPlayer, NetEaseMusic, NetEaseMusicNodeJS, TXQQMusic
from .types import MusicServiceConfig, PickStrategy


@dataclass(slots=True)
class Plan:
    search_query: str
    need_web_search: bool = False
    pick_strategy: PickStrategy = "random"
    platform_hint: Optional[str] = None

    # 方案B：LLM 决定搜什么
    web_queries: Optional[list[str]] = None

    # A：语境识别
    context_style: Optional[str] = None  # meme|anime|game|music|general
    domain_hint: Optional[str] = None  # bilibili|zhihu|moegirl|wiki|null


class MusicService:
    """
    AI 助手点歌 Service（ABCD 拉满）：
    A) 语境识别（meme/anime/game/music/general）+ 站点倾向
    B) LLM 决定 web_queries（不再“把原句丢进搜索”）
    C) Tavily include_domains 站点倾向/白名单（可配置开启）
    D) 搜索结果质量检测 + 失败时最多重试 N 次（默认 1）
    """

    # --- domain bias presets ---
    _DOMAINS_MEME = ["bilibili.com", "moegirl.org.cn", "zhihu.com", "baike.baidu.com", "wikipedia.org"]
    _DOMAINS_MUSIC = ["music.163.com", "y.qq.com", "zhihu.com", "baike.baidu.com", "wikipedia.org"]
    _DOMAINS_GAME = ["bilibili.com", "zhihu.com", "baike.baidu.com", "wikipedia.org"]
    _DOMAINS_ANIME = ["bilibili.com", "moegirl.org.cn", "zhihu.com", "baike.baidu.com", "wikipedia.org"]

    # --- quality keywords (for D) ---
    _QUALITY_KW = [
        "BGM",
        "bgm",
        "原曲",
        "原唱",
        "配乐",
        "OST",
        "ost",
        "歌名",
        "歌曲",
        "歌词",
        "音译",
        "出处",
        "梗",
        "口癖",
        "台词",
        "片段",
    ]

    def __init__(self, cfg: MusicServiceConfig, *, data_dir_name: str = "ai_assistant_music"):
        self.cfg = cfg

        self.data_dir = localstore.get_data_dir(data_dir_name)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.songs_dir = self.data_dir / "songs"
        self.db_path = self.data_dir / "playlist.db"

        self.font_path = Path(__file__).resolve().parent / "fonts" / "simhei.ttf"

        self.players: list[BaseMusicPlayer] = []
        self.keywords: list[str] = []

        self.downloader: Downloader | None = None
        self.sender: MusicSender | None = None
        self.playlist: Playlist | None = None

        self._init_lock = asyncio.Lock()
        self._inited = False

    async def ensure_inited(self) -> None:
        async with self._init_lock:
            if self._inited:
                return

            # 注册平台（触发 subclass register）
            _ = (NetEaseMusic, NetEaseMusicNodeJS, TXQQMusic)

            self.players.clear()
            self.keywords.clear()
            for cls in BaseMusicPlayer.get_all_subclass():
                try:
                    self.players.append(cls(self.cfg))
                    self.keywords.extend(cls.platform.keywords)
                except Exception as e:
                    logger.error(f"[ai_assistant.music] init player {cls.__name__} failed: {e}")

            self.downloader = Downloader(self.cfg, self.songs_dir)
            await self.downloader.initialize()

            renderer = MusicRenderer(self.font_path)
            self.sender = MusicSender(self.cfg, renderer, self.downloader)

            self.playlist = Playlist(self.db_path, limit=self.cfg.playlist_limit)
            await self.playlist.initialize()

            self._inited = True
            logger.info("[ai_assistant.music] inited")

    async def shutdown(self) -> None:
        async with self._init_lock:
            if not self._inited:
                return
            for p in self.players:
                try:
                    await p.close()
                except Exception:
                    pass
            if self.downloader:
                try:
                    await self.downloader.close()
                except Exception:
                    pass
            if self.playlist:
                try:
                    await self.playlist.close()
                except Exception:
                    pass
            self._inited = False

    def get_player(
        self,
        *,
        name: str | None = None,
        word: str | None = None,
        default: bool = False,
    ) -> BaseMusicPlayer | None:
        if default:
            name = self.cfg.default_player_name
            word = self.cfg.default_player_name

        for p in self.players:
            if name:
                name_ = name.strip().lower()
                if p.platform.display_name.lower() == name_ or p.platform.name.lower() == name_:
                    return p
            if word:
                w = word.strip().lower()
                for kw in p.platform.keywords:
                    if kw.lower() in w:
                        return p
        return None

    @staticmethod
    def _safe_parse_json(text: str) -> Optional[dict]:
        t = (text or "").strip()
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I).strip()
        t = re.sub(r"\s*```$", "", t).strip()
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
        return None

    def _normalize_web_queries(self, raw_q: object) -> Optional[list[str]]:
        if not isinstance(raw_q, list):
            return None
        cleaned: list[str] = []
        for q in raw_q:
            if not isinstance(q, str):
                continue
            q = q.strip()
            if not q:
                continue
            if len(q) > 60:
                q = q[:60].strip()
            if q not in cleaned:
                cleaned.append(q)
        return cleaned[:3] if cleaned else None

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
        # 拼接 title + content 做简单检测
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
        # 命中 >=2 视为“质量可用”
        return hit >= 2

    async def _llm_plan(self, raw_request: str, *, extra_context: str | None = None) -> Plan:
        """
        A+B：意图解析 + 语境识别 + 生成 web_queries（慢路径专用）
        - 若带 web_context：必须输出 need_web_search=false（避免循环）
        """
        system = (
            "你是点歌请求解析器。目标：最终能找到一首歌并播放。\n"
            "只输出 JSON（不要输出任何解释/Markdown）。\n"
            "schema:\n"
            '{"search_query":"string","need_web_search":true|false,"web_queries":["string"],'
            '"pick_strategy":"random|first|best_match","platform_hint":"netease|netease_nodejs|txqq|null",'
            '"context_style":"meme|anime|game|music|general","domain_hint":"bilibili|zhihu|moegirl|wiki|null"}\n'
            "要求：\n"
            "- search_query 尽量短（<=40字），用于直接搜歌（歌手/歌名/风格/口癖/BGM 关键词）。\n"
            "- 当请求含不明梗/外语音译/实体不清时 need_web_search=true，否则 false。\n"
            "- 如果 need_web_search=true：必须提供 web_queries（1~3条，<=60字）。\n"
            "  web_queries 必须至少包含 1 条“指向歌曲”的 query，带关键词之一：BGM/原曲/是什么歌/音译/歌词/配乐/OST。\n"
            "- 如果已经提供 web_context：need_web_search 必须为 false，web_queries 置空或不提供。\n"
            "- context_style：判断语境（meme/anime/game/music/general），用于更聪明的检索。\n"
            "- domain_hint：可选站点倾向（bilibili/zhihu/moegirl/wiki），不确定则 null。\n"
            "- 用户说随便/随机/来一首时 pick_strategy=random。\n"
            "- platform_hint 除非用户明确指定平台，否则为 null。\n"
            "\nFew-shot:\n"
            'raw="放一首黄色奶龙的shukishuki"\n'
            '{"search_query":"黄色奶龙 shukishuki BGM","need_web_search":true,'
            '"web_queries":["黄色奶龙 梗 出处 BGM","shukishuki 是什么歌 音译 歌词","黄色奶龙 shukishuki 原曲"],'
            '"pick_strategy":"random","platform_hint":null,"context_style":"meme","domain_hint":"bilibili"}\n'
            'raw="点歌 给我随便来一首陶喆的歌"\n'
            '{"search_query":"陶喆 热门 歌曲","need_web_search":false,"web_queries":[],'
            '"pick_strategy":"random","platform_hint":null,"context_style":"music","domain_hint":null}\n'
            'raw="点歌 来一首二次元常用的嘛嘛嘛那首"\n'
            '{"search_query":"嘛嘛嘛 二次元 BGM","need_web_search":true,'
            '"web_queries":["二次元 嘛嘛嘛 口癖 出处 BGM","嘛嘛嘛 是什么歌 歌词","嘛嘛嘛 音译 原曲"],'
            '"pick_strategy":"random","platform_hint":null,"context_style":"anime","domain_hint":"moegirl"}\n'
        )

        user = f"raw_request={raw_request}"
        if extra_context:
            user += "\n\n[web_context]\n" + extra_context

        content, _, _ = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=260,
            temperature=self.cfg.llm_temperature,
            top_p=self.cfg.llm_top_p,
        )

        obj = self._safe_parse_json(content)
        if not obj:
            # 兜底：不用联网，直接用原文做搜索
            return Plan(search_query=raw_request.strip()[:40] or raw_request, need_web_search=False)

        search_query = str(obj.get("search_query") or "").strip()
        if not search_query:
            search_query = raw_request.strip()[:40] or raw_request

        # 如果提供了 web_context，不允许继续要求联网
        need_web_search = False if extra_context else bool(obj.get("need_web_search", False))

        pick_strategy = str(obj.get("pick_strategy") or self.cfg.pick_default).strip().lower()
        if pick_strategy not in {"random", "first", "best_match"}:
            pick_strategy = "random"

        platform_hint = obj.get("platform_hint")
        if platform_hint is not None:
            platform_hint = str(platform_hint).strip() or None
        if platform_hint not in {None, "netease", "netease_nodejs", "txqq"}:
            platform_hint = None

        context_style = obj.get("context_style")
        if context_style is not None:
            context_style = str(context_style).strip() or None
        if context_style not in {None, "meme", "anime", "game", "music", "general"}:
            context_style = None

        domain_hint = obj.get("domain_hint")
        if domain_hint is not None:
            domain_hint = str(domain_hint).strip() or None
        if domain_hint not in {None, "bilibili", "zhihu", "moegirl", "wiki"}:
            domain_hint = None

        web_queries = None if extra_context else self._normalize_web_queries(obj.get("web_queries"))

        return Plan(
            search_query=search_query,
            need_web_search=need_web_search and self.cfg.allow_web_search,
            pick_strategy=pick_strategy,  # type: ignore[arg-type]
            platform_hint=platform_hint,
            web_queries=web_queries,
            context_style=context_style,
            domain_hint=domain_hint,
        )

    async def _llm_expand_web_queries(self, raw_request: str, *, plan: Plan, reason: str) -> list[str]:
        """
        D：结果质量差时，让 LLM 再生成一组更强的 web_queries（偏向“找到歌曲/BGM/原曲”）。
        """
        system = (
            "你是联网检索 query 生成器。目标：为了找到“对应的歌曲/BGM/原曲/歌词/音译”。\n"
            "只输出 JSON：{ \"web_queries\": [\"...\"] }\n"
            "规则：\n"
            "- 输出 1~3 条 query，每条 <=60 字。\n"
            "- 必须至少 1 条包含关键词之一：BGM/原曲/是什么歌/音译/歌词/配乐/OST。\n"
            "- 若语境是 meme/anime/game，请加入：梗/出处/口癖/台词 之一。\n"
            "- 避免泛问法（不要只写“什么是X”），要带语境限定（如“二次元语境/玩梗/出处/BGM”）。\n"
        )
        user = (
            f"raw_request={raw_request}\n"
            f"context_style={plan.context_style}\n"
            f"previous_queries={plan.web_queries}\n"
            f"reason={reason}\n"
        )

        content, _, _ = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=180,
            temperature=self.cfg.llm_temperature,
            top_p=self.cfg.llm_top_p,
        )
        obj = self._safe_parse_json(content) or {}
        qs = self._normalize_web_queries(obj.get("web_queries"))
        if qs:
            return qs
        # 兜底
        return [raw_request[:60], f"{raw_request[:40]} 原曲 是什么歌", f"{raw_request[:40]} BGM"]

    async def _multi_search(
        self,
        queries: list[str],
        *,
        include_domains: Optional[list[str]] = None,
    ) -> list[dict]:
        merged: list[dict] = []
        seen = set()

        # 按全局配置分配 max_results
        total_max = 5
        try:
            from ..config import plugin_config

            total_max = int(getattr(plugin_config, "web_search_max_results", 5) or 5)
        except Exception:
            total_max = 5
        if total_max < 1:
            total_max = 5

        per_query = max(1, int((total_max + len(queries) - 1) / max(1, len(queries))))

        for q in queries:
            try:
                rs = await tavily_search(q, max_results=per_query, include_domains=include_domains)
            except Exception as e:
                logger.warning(f"[ai_assistant.music] Tavily 搜索失败: query={q!r} err={e}")
                continue

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
        C+D：站点倾向 + 质量检测 + 重试
        返回 format_search_results 文本（用于喂给第二次 _llm_plan）。
        """
        qlist = plan.web_queries or [raw_request[:60]]

        include_domains = self._pick_domain_bias(plan) if self.cfg.web_search_domain_bias_enabled else None

        # 1) 先带 domain bias 搜
        results = await self._multi_search(qlist, include_domains=include_domains)

        # 2) 如果带 bias 搜不到或质量很差，放宽域名再来一次（不计入 retry_times）
        if (not results) or (not self._is_search_good(results)):
            if include_domains:
                relaxed = await self._multi_search(qlist, include_domains=None)
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
            # 扩展查询：优先不加域名限制，避免越限越偏
            results2 = await self._multi_search(qlist2, include_domains=None)

            # 合并并判断是否变好
            combined = results[:]  # copy
            seen = set((r.get("url") or "") + (r.get("title") or "") for r in combined)
            for r in results2:
                k = (r.get("url") or "") + (r.get("title") or "")
                if k in seen:
                    continue
                combined.append(r)
                seen.add(k)
            # 取更好的结果集合（优先质量，其次数量）
            if self._is_search_good(combined) or (len(combined) > len(results)):
                results = combined

        if not results:
            return None
        return format_search_results(results, max_chars=1200)

    async def search_songs(
        self,
        keyword: str,
        *,
        platform_hint: str | None = None,
    ) -> tuple[BaseMusicPlayer, list[Song]] | tuple[None, list[Song]]:
        await self.ensure_inited()

        player: BaseMusicPlayer | None = None
        if platform_hint:
            player = self.get_player(name=platform_hint)
        if not player:
            player = self.get_player(default=True)
        if not player:
            return None, []

        limit = max(1, min(int(self.cfg.candidate_limit), 50))
        songs = await player.fetch_songs(keyword=keyword, limit=limit)
        return player, songs

    def pick_song(self, songs: list[Song], *, strategy: PickStrategy, raw_request: str) -> Song | None:
        if not songs:
            return None

        if strategy == "first":
            return songs[0]
        if strategy == "best_match":
            text = raw_request.lower()
            scored = []
            for s in songs:
                name = (s.name or "").lower()
                artists = (s.artists or "").lower()
                score = 0
                for token in set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text)):
                    if token in name:
                        score += 3
                    if token in artists:
                        score += 2
                scored.append((score, s))
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1] if scored else songs[0]

        return random.choice(songs)

    async def play_by_natural_language(self, bot: Bot, event: MessageEvent, raw_request: str) -> str:
        """
        主入口：由 ai_assistant matcher 调用。
        返回：
        - 空字符串：表示已在内部发完 Now Playing + 歌曲，不需要 matcher 再输出
        - 非空：错误说明（matcher 会 finish 输出）
        """
        await self.ensure_inited()

        raw_request = remove_markdown(raw_request).strip()
        if not raw_request:
            return "你想听什么歌？例如：点歌 给我来一首周杰伦的歌"

        # 先给用户即时反馈
        try:
            await bot.send(event, self.cfg.fast_path_hint)
        except Exception:
            pass

        # 1) LLM 决定是否需要联网 + 生成 plan（含 web_queries / context_style / domain_hint）
        plan = await self._llm_plan(raw_request)

        # 2) 若需要联网：先安抚，再按 web_queries 做 Tavily 搜索（带站点倾向/质量重试），再让 LLM 基于 context 得到更好的 search_query
        if plan.need_web_search:
            try:
                await bot.send(event, self.cfg.slow_path_hint)
            except Exception:
                pass

            try:
                context = await self._web_search_slow_path(raw_request, plan)
            except Exception as e:
                logger.warning(f"[ai_assistant.music] web search failed: {e}")
                context = None

            if context:
                plan = await self._llm_plan(raw_request, extra_context=context)

        # 3) 搜索歌曲
        player, songs = await self.search_songs(plan.search_query, platform_hint=plan.platform_hint)
        if not player or not songs:
            return f"没找到符合“{plan.search_query}”的歌曲，要不要换个说法？"

        # 4) 选一首
        song = self.pick_song(songs, strategy=plan.pick_strategy, raw_request=raw_request)
        if not song:
            return "没找到合适的歌。"

        # 5) 先发送 Now Playing
        now_text = f"Now Playing: {song.name}-{song.artists}"
        try:
            await bot.send(event, now_text)
        except Exception:
            pass

        # 6) 再发送歌曲（卡片/语音/文件/文本）
        assert self.sender is not None
        ctx = SendContext(bot=bot, event=event)
        ok = await self.sender.send_song(ctx, player, song)
        if not ok:
            return "歌曲发送失败（已发送 Now Playing，但歌曲内容未能发出）。"

        return ""
