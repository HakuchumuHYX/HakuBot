from __future__ import annotations

import asyncio
from pathlib import Path

from nonebot import require
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as localstore

from ...utils import remove_markdown
from .core import Downloader, MusicRenderer, MusicSender, Playlist, SendContext
from .core.model import Song
from .core.platform import BaseMusicPlayer, NetEaseMusic, NetEaseMusicNodeJS, TXQQMusic
from .types import MusicServiceConfig, PickStrategy

from .logic.fast_path import FastPathMixin
from .logic.logging import LoggingMixin
from .logic.llm_plan import LLMPlanMixin
from .logic.models import Plan
from .logic.song_search import SongSearchMixin, _cjk_fuzzy_match
from .logic.web_search import WebSearchMixin


class MusicService(LoggingMixin, LLMPlanMixin, WebSearchMixin, FastPathMixin, SongSearchMixin):
    """
    AI 助手点歌 Service（全面优化版）：
    A) 语境识别（meme/anime/game/music/general）+ 站点倾向
    B) LLM 决定 web_queries + 多变体搜索词 + 置信度评估
    C) Tavily include_domains 站点倾向/白名单（可配置开启）
    D) 搜索结果质量检测 + LLM相关性验证 + 失败时最多重试 N 次
    E) 快路径并行执行 + 超时降级 + 拼音匹配
    """

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

    async def play_by_natural_language(self, bot: Bot, event: MessageEvent, raw_request: str) -> str:
        """
        主入口：由 ai_assistant matcher 调用。
        返回：
        - 空字符串：表示已在内部发完 Now Playing + 歌曲，不需要 matcher 再输出
        - 非空：错误说明（matcher 会 finish 输出）
        """
        await self.ensure_inited()

        raw_request = remove_markdown(raw_request).strip()
        
        # 初始化日志会话（debug 模式下每次请求一个独立日志文件）
        self._init_log_session(raw_request)
        
        self._log(
            "music.play.entry",
            {
                "raw_request": raw_request,
                "cfg": {
                    "allow_web_search": self.cfg.allow_web_search,
                    "candidate_limit": self.cfg.candidate_limit,
                    "pick_default": self.cfg.pick_default,
                    "default_player_name": self.cfg.default_player_name,
                    "web_search_retry_times": self.cfg.web_search_retry_times,
                    "web_search_domain_bias_enabled": self.cfg.web_search_domain_bias_enabled,
                    "fast_path_confidence_threshold": getattr(self.cfg, "fast_path_confidence_threshold", 70),
                    "enable_llm_relevance_check": getattr(self.cfg, "enable_llm_relevance_check", True),
                    "enable_alternative_queries": getattr(self.cfg, "enable_alternative_queries", True),
                    "enable_parallel_fast_path": getattr(self.cfg, "enable_parallel_fast_path", True),
                },
            },
        )

        if not raw_request:
            self._flush_log()
            return "你想听什么歌？例如：点歌 给我来一首周杰伦的歌"

        # 先给用户即时反馈
        try:
            await bot.send(event, self.cfg.fast_path_hint)
        except Exception:
            pass

        # 是否启用并行快路径
        enable_parallel = getattr(self.cfg, "enable_parallel_fast_path", True)

        try:
            if enable_parallel:
                # 并行执行：快路径搜索同时启动LLM解析
                result = await self._parallel_fast_path(bot, event, raw_request)
                if result is not None:
                    return result
            else:
                # 串行执行（兼容旧逻辑）
                result = await self._serial_fast_path(bot, event, raw_request)
                if result is not None:
                    return result

            return ""
        finally:
            # 确保日志总是被写入文件
            self._flush_log()

    async def _parallel_fast_path(self, bot: Bot, event: MessageEvent, raw_request: str) -> str | None:
        """
        并行快路径：同时执行预搜索、快判、LLM解析
        返回 None 表示已成功播放，返回字符串表示错误信息
        """
        # 同时启动预搜索和LLM解析
        presearch_task = asyncio.create_task(self._netease_presearch(raw_request))
        
        # LLM解析（带超时降级）
        llm_timeout = self.cfg.llm_timeout_fallback
        llm_timeout_seconds = self.cfg.llm_timeout_seconds
        llm_failed = False  # 标记 LLM 是否失败/超时
        try:
            plan_task = asyncio.create_task(self._llm_plan(raw_request))
            # 给LLM更多时间（默认15秒）
            plan = await asyncio.wait_for(plan_task, timeout=llm_timeout_seconds)
        except asyncio.TimeoutError:
            llm_failed = True
            if llm_timeout:
                self._log("music.play.llm_timeout", {"raw_request": raw_request, "timeout_seconds": llm_timeout_seconds})
                # 超时降级：尝试基本的中文意译识别
                fallback_query, fallback_reason = self._fallback_query_from_chinese(raw_request)
                plan = Plan(
                    search_query=fallback_query,
                    need_web_search=True,  # 超时时应该允许联网
                    confidence=30,
                    parse_reason=f"LLM超时({llm_timeout_seconds}s)，{fallback_reason}",
                )
            else:
                # 重新等待LLM
                plan = await self._llm_plan(raw_request)
        except Exception as e:
            llm_failed = True
            self._log("music.play.llm_error", {"error": str(e)})
            # 错误降级：也尝试基本的中文意译识别
            fallback_query, fallback_reason = self._fallback_query_from_chinese(raw_request)
            plan = Plan(
                search_query=fallback_query,
                need_web_search=True,  # 错误时也应该允许联网
                confidence=30,
                parse_reason=f"LLM错误: {e}，{fallback_reason}",
            )

        self._log("music.play.plan1", self._plan_dump(plan))

        # 等待预搜索完成
        pre_player, pre_songs = await presearch_task

        # 快路径判定
        if pre_songs:
            try:
                accept, pick_index, confidence, reason = await self._llm_fast_gate(raw_request, pre_songs)
            except Exception as e:
                self._log("music.fast_gate.error", {"raw_request": raw_request, "error": str(e)})
                accept, pick_index, confidence, reason = False, None, 0, str(e)

            self._log(
                "music.play.fast_path.gate",
                {
                    "raw_request": raw_request,
                    "accept": accept,
                    "pick_index": pick_index,
                    "confidence": confidence,
                    "reason": reason,
                    "candidate_count": len(pre_songs),
                },
            )

            if accept:
                player = pre_player
                if not player:
                    return "当前没有可用的音乐平台（网易云不可用）。"

                # 选歌
                song: Song | None = None
                if pick_index is not None and 1 <= pick_index <= len(pre_songs):
                    song = pre_songs[pick_index - 1]
                if not song:
                    song = self.pick_song(pre_songs, strategy="best_match", raw_request=raw_request) or pre_songs[0]

                # 新增：歌手一致性验证
                # 当 LLM 解析出明确的 song_artist 时，验证快速路径选中的歌是否与之匹配
                # 如果不匹配，降级到慢路径以获得更准确的搜索结果
                if plan.song_artist and song:
                    song_artists = song.artists or ""
                    artist_match = _cjk_fuzzy_match(plan.song_artist, song_artists)
                    
                    if not artist_match:
                        self._log(
                            "music.play.fast_path.artist_mismatch",
                            {
                                "raw_request": raw_request,
                                "expected_artist": plan.song_artist,
                                "actual_artists": song_artists,
                                "picked_song": song.name,
                                "action": "降级到慢路径",
                            },
                        )
                        # 不接受快速路径，走慢路径
                        return await self._slow_path(bot, event, raw_request, plan)

                self._log(
                    "music.play.fast_path.hit",
                    {
                        "raw_request": raw_request,
                        "confidence": confidence,
                        "reason": reason,
                        "picked": {
                            "id": song.id,
                            "name": song.name,
                            "artists": song.artists,
                        },
                    },
                )

                return await self._send_song(bot, event, player, song)

        # 快路径未命中，走慢路径
        return await self._slow_path(bot, event, raw_request, plan)

    async def _serial_fast_path(self, bot: Bot, event: MessageEvent, raw_request: str) -> str | None:
        """
        串行快路径（兼容旧逻辑）
        """
        # 1) 快路径
        pre_player, pre_songs = await self._netease_presearch(raw_request)
        if pre_songs:
            try:
                accept, pick_index, confidence, reason = await self._llm_fast_gate(raw_request, pre_songs)
            except Exception as e:
                self._log("music.fast_gate.error", {"raw_request": raw_request, "error": str(e)})
                accept, pick_index, confidence, reason = False, None, 0, str(e)

            self._log(
                "music.play.fast_path.gate",
                {
                    "raw_request": raw_request,
                    "accept": accept,
                    "pick_index": pick_index,
                    "confidence": confidence,
                    "reason": reason,
                    "candidate_count": len(pre_songs),
                },
            )

            if accept:
                player = pre_player
                if not player:
                    return "当前没有可用的音乐平台（网易云不可用）。"

                song: Song | None = None
                if pick_index is not None and 1 <= pick_index <= len(pre_songs):
                    song = pre_songs[pick_index - 1]
                if not song:
                    song = self.pick_song(pre_songs, strategy="best_match", raw_request=raw_request) or pre_songs[0]

                return await self._send_song(bot, event, player, song)

        # 2) 慢路径
        plan = await self._llm_plan(raw_request)
        self._log("music.play.plan1", self._plan_dump(plan))
        return await self._slow_path(bot, event, raw_request, plan)

    async def _slow_path(self, bot: Bot, event: MessageEvent, raw_request: str, plan: Plan) -> str | None:
        """
        慢路径：LLM解析 + 可选联网 + 多变体搜索 + LLM相关性验证
        优化：如果从 web_context 能直接提取出歌名，可跳过 LLM 重解析
        """
        web_context_used = False

        # 若需要联网
        if plan.need_web_search:
            try:
                await bot.send(event, self.cfg.slow_path_hint)
            except Exception:
                pass

            try:
                context = await self._web_search_slow_path(raw_request, plan)
            except Exception as e:
                logger.warning(f"[ai_assistant.music] web search failed: {e}")
                self._log("music.play.web_search.error", {"error": str(e)})
                context = None

            self._log("music.play.web_search.context", context or "")

            if context:
                # 常规路径：调用 LLM 重解析（基于 web_context）
                plan = await self._llm_plan(raw_request, extra_context=context)
                self._log("music.play.plan2", self._plan_dump(plan))
                web_context_used = True

        # 搜索歌曲（支持多变体搜索）
        try:
            player, songs, lowq = await self.search_songs(
                plan.search_query,
                platform_hint=None,
                extra=raw_request,
                song_title=plan.song_title,
                song_artist=plan.song_artist,
                alternative_queries=plan.alternative_queries,
                raw_request=raw_request,
            )
        except Exception as e:
            logger.exception(f"[ai_assistant.music] search_songs failed: {e}")
            self._log(
                "music.play.search_songs.error",
                {"error": str(e), "search_query": plan.search_query, "raw_request": raw_request},
            )
            return "搜歌失败了（内部异常）。"

        self._log(
            "music.play.search_songs",
            {
                "search_query": plan.search_query,
                "alternative_queries": plan.alternative_queries,
                "platform_hint": plan.platform_hint,
                "player": None
                if not player
                else {"name": player.platform.name, "display_name": player.platform.display_name},
                "song_count": len(songs),
                "low_quality": lowq,
                "web_context_used": web_context_used,
                "confidence": plan.confidence,
                "parse_reason": plan.parse_reason,
            },
        )

        if not player:
            return "当前没有可用的音乐平台（网易云不可用）。"

        # 若搜不到或明显不相关：触发一次慢路径纠错
        if (not songs) or lowq:
            self._log(
                "music.play.low_quality.detected",
                {
                    "raw_request": raw_request,
                    "search_query": plan.search_query,
                    "song_title": plan.song_title,
                    "song_artist": plan.song_artist,
                    "song_count": len(songs),
                    "web_context_used": web_context_used,
                },
            )

            if self.cfg.allow_web_search and not web_context_used:
                try:
                    await bot.send(event, self.cfg.slow_path_hint)
                except Exception:
                    pass

                try:
                    context2 = await self._web_search_slow_path(raw_request, plan)
                except Exception as e:
                    logger.warning(f"[ai_assistant.music] web search failed (recovery): {e}")
                    self._log("music.play.web_search.recovery_error", {"error": str(e)})
                    context2 = None

                self._log("music.play.web_search.recovery_context", context2 or "")

                if context2:
                    plan2 = await self._llm_plan(raw_request, extra_context=context2)
                    self._log("music.play.plan_recovery", self._plan_dump(plan2))
                    plan = plan2
                    web_context_used = True

                    try:
                        player, songs, lowq = await self.search_songs(
                            plan.search_query,
                            platform_hint=None,
                            extra=raw_request,
                            song_title=plan.song_title,
                            song_artist=plan.song_artist,
                            alternative_queries=plan.alternative_queries,
                            raw_request=raw_request,
                        )
                    except Exception as e:
                        logger.exception(f"[ai_assistant.music] search_songs failed (recovery): {e}")
                        self._log(
                            "music.play.search_songs.recovery_error",
                            {"error": str(e), "search_query": plan.search_query, "raw_request": raw_request},
                        )
                        return "搜歌失败了（内部异常）。"

                    self._log(
                        "music.play.search_songs.recovery_result",
                        {
                            "search_query": plan.search_query,
                            "song_count": len(songs),
                            "low_quality": lowq,
                        },
                    )

            # 纠错后仍然"搜歪/无结果"则停止
            if (not songs) or lowq:
                self._log(
                    "music.play.abort_low_quality",
                    {
                        "raw_request": raw_request,
                        "final_search_query": plan.search_query,
                        "song_title": plan.song_title,
                        "song_artist": plan.song_artist,
                        "song_count": len(songs),
                    },
                )
                # 改进的失败提示
                suggestions = self._generate_failure_suggestions(raw_request, plan)
                return f"搜索结果与「{raw_request}」可能不太相关。{suggestions}"

        # 选一首（传入 song_title/song_artist 以优先匹配歌名）
        song = self.pick_song(
            songs,
            strategy=plan.pick_strategy,
            raw_request=raw_request,
            song_title=plan.song_title,
            song_artist=plan.song_artist,
        )
        self._log(
            "music.play.pick_song",
            {
                "strategy": plan.pick_strategy,
                "picked": None
                if not song
                else {
                    "id": song.id,
                    "name": song.name,
                    "artists": song.artists,
                },
            },
        )
        if not song:
            return "没找到合适的歌。"

        return await self._send_song(bot, event, player, song)

    async def _send_song(self, bot: Bot, event: MessageEvent, player: BaseMusicPlayer, song: Song) -> str | None:
        """
        发送歌曲（Now Playing + 歌曲内容）
        返回 None 表示成功，返回字符串表示错误
        """
        # 发送 Now Playing（回复用户的点歌消息，方便多人同时点歌时区分）
        now_text = f"Now Playing: {song.name}-{song.artists}"
        try:
            reply_msg = MessageSegment.reply(event.message_id) + now_text
            await bot.send(event, reply_msg)
        except Exception:
            pass

        # 发送歌曲
        assert self.sender is not None
        ctx = SendContext(bot=bot, event=event)
        try:
            ok = await self.sender.send_song(ctx, player, song)
        except Exception as e:
            logger.exception(f"[ai_assistant.music] send_song failed: {e}")
            self._log(
                "music.play.send_song.error",
                {
                    "error": str(e),
                    "player": {"name": player.platform.name, "display_name": player.platform.display_name},
                    "song": {"id": song.id, "name": song.name, "artists": song.artists},
                },
            )
            ok = False

        self._log(
            "music.play.send_song",
            {
                "ok": ok,
                "player": {"name": player.platform.name, "display_name": player.platform.display_name},
                "song": {"id": song.id, "name": song.name, "artists": song.artists},
            },
        )

        if not ok:
            return "歌曲发送失败（已发送 Now Playing，但歌曲内容未能发出）。"

        return None  # 成功

    def _fallback_query_from_chinese(self, raw_request: str) -> tuple[str, str]:
        """
        LLM超时/失败时的降级处理：尝试基本的中文意译识别。
        
        返回: (fallback_query, reason)
        """
        import re
        
        # 中文意译对照表（常见的逐字翻译模式）
        CHINESE_TO_ENGLISH = {
            # 天体/自然
            "月": ["luna", "moon", "tsuki"],
            "日": ["sun", "sol", "hi"],
            "星": ["star", "stella", "hoshi"],
            "雪": ["snow", "yuki"],
            "雨": ["rain", "ame"],
            "风": ["wind", "kaze"],
            "火": ["fire", "hi"],
            "水": ["water", "mizu"],
            "花": ["flower", "hana"],
            "樱": ["sakura", "cherry"],
            "空": ["sky", "sora"],
            "海": ["sea", "ocean", "umi"],
            "光": ["light", "hikari"],
            "影": ["shadow", "kage"],
            "夜": ["night", "yoru"],
            "梦": ["dream", "yume"],
            
            # 动作/状态
            "说": ["say", "speak", "tell"],
            "唱": ["sing", "song"],
            "跳": ["dance", "jump"],
            "飞": ["fly", "flying"],
            "走": ["walk", "go"],
            "跑": ["run", "running"],
            "笑": ["smile", "laugh"],
            "哭": ["cry", "crying"],
            "爱": ["love", "ai"],
            "恨": ["hate"],
            "想": ["think", "miss", "want"],
            "看": ["see", "look", "watch"],
            "听": ["hear", "listen"],
            
            # 情感/形容
            "美": ["beautiful", "beauty"],
            "丽": ["beautiful", "pretty"],
            "好": ["good", "nice"],
            "坏": ["bad"],
            "快": ["fast", "happy"],
            "慢": ["slow"],
            "大": ["big", "large", "great"],
            "小": ["small", "little"],
            "高": ["high", "tall"],
            "低": ["low"],
            "长": ["long"],
            "短": ["short"],
            "新": ["new"],
            "旧": ["old"],
            "真": ["true", "real"],
            "假": ["false", "fake"],
            
            # 其他常用
            "也许": ["maybe", "perhaps"],
            "永远": ["forever", "eternal"],
            "时间": ["time"],
            "世界": ["world", "sekai"],
            "天使": ["angel", "tenshi"],
            "恶魔": ["demon", "devil"],
            "王": ["king", "ou"],
            "女王": ["queen"],
            "公主": ["princess", "hime"],
            "王子": ["prince", "ouji"],
            "战士": ["warrior", "soldier"],
            "英雄": ["hero"],
            "命运": ["fate", "destiny"],
            "约定": ["promise", "yakusoku"],
            "回忆": ["memory", "memories"],
            "未来": ["future", "mirai"],
            "过去": ["past"],
            "现在": ["now", "present"],
        }
        
        # 清理输入
        text = raw_request.strip()
        # 移除常见前缀
        for prefix in ["来首", "点歌", "播放", "给我来", "我要听", "想听"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        
        # 移除歌手信息（如"xxx的"）
        artist_match = re.match(r"^(.+?)的(.+)$", text)
        artist_part = ""
        if artist_match:
            artist_part = artist_match.group(1)
            text = artist_match.group(2)
        
        # 尝试识别意译
        translations = []
        remaining = text
        
        # 先尝试多字词
        for cn, en_list in sorted(CHINESE_TO_ENGLISH.items(), key=lambda x: -len(x[0])):
            if cn in remaining:
                translations.append((cn, en_list[0]))  # 取第一个翻译
                remaining = remaining.replace(cn, "", 1)
        
        if translations:
            # 构建翻译后的查询
            translated_parts = [en for _, en in translations]
            translated_query = " ".join(translated_parts)
            
            # 如果有歌手信息，也加上
            if artist_part:
                translated_query = f"{translated_query} {artist_part}"
            
            reason = f"意译识别: {' + '.join([f'{cn}→{en}' for cn, en in translations])}"
            return translated_query, reason
        
        # 没有识别到意译，使用原始请求的前40个字符
        fallback = raw_request.strip()[:40] or raw_request
        return fallback, "使用原始输入"

    def _generate_failure_suggestions(self, raw_request: str, plan: Plan) -> str:
        """
        生成失败时的建议提示
        """
        suggestions = []
        
        # 基于解析结果给出建议
        if plan.song_title:
            suggestions.append(f"尝试直接搜索歌名「{plan.song_title}」")
        if plan.song_artist:
            suggestions.append(f"或者试试「{plan.song_artist}的歌」")
        if plan.alternative_queries:
            alt = plan.alternative_queries[0] if plan.alternative_queries else None
            if alt:
                suggestions.append(f"也可以试试「{alt}」")
        
        if not suggestions:
            suggestions.append("你可以试试提供更准确的歌名或歌手名")
        
        return "\n建议：" + "；".join(suggestions[:2]) + "。"
