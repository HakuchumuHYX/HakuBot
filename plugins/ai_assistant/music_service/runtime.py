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

from ..config import plugin_config
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

    # LLM 抽取：歌名 / 歌手（用于强制“歌名在前歌手在后”的检索）
    song_title: Optional[str] = None
    song_artist: Optional[str] = None

    # 方案B：LLM 决定搜什么
    web_queries: Optional[list[str]] = None

    # A：语境识别
    context_style: Optional[str] = None  # meme|anime|game|music|general
    domain_hint: Optional[str] = None  # bilibili|zhihu|moegirl|wiki|null


def _dbg(text: str, *, max_chars: int) -> str:
    t = (text or "").replace("\r", "").strip()
    if max_chars and len(t) > max_chars:
        return t[:max_chars] + f"...(truncated, total={len(t)})"
    return t


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

    def _rank_web_results(self, results: list[dict], *, plan: Plan) -> list[dict]:
        """
        把“像歌曲页/包含原名信息”的结果排到前面，避免 format_search_results 截断后只剩人物页。
        """
        title_tokens = self._web_tokenize(plan.song_title)
        artist_tokens = self._web_tokenize(plan.song_artist)

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

        def _score(r: dict) -> int:
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip()
            text = f"{title}\n{content}".lower()

            score = 0
            for kw in songish_kws:
                if kw.lower() in text:
                    score += 2
            for tok in title_tokens:
                if tok.lower() in text:
                    score += 5
            for tok in artist_tokens:
                if tok.lower() in text:
                    score += 3

            # 轻惩罚人物百科类（防止压过歌曲页）
            if ("萌娘百科" in title or "百科" in title) and ("曲目" not in content) and ("原創曲目" not in content) and ("原创曲目" not in content):
                score -= 2

            return score

        ranked = sorted([r for r in (results or []) if isinstance(r, dict)], key=_score, reverse=True)
        return ranked

    @staticmethod
    def _extract_song_title_from_web_context(web_context: str) -> str | None:
        """
        从 web_context 里提取“更可靠的正式歌名”，用于覆盖 LLM 规划时的 song_title。
        目标：解决“联网明明给出了正式歌名，但二次仍用模糊片段去搜”的问题。

        web_context 通常来自 format_search_results，形如：
        [1] 标题
        url
        摘要：...
        """
        if not web_context:
            return None

        text = web_context.strip()

        def _clean_title(t: str) -> str | None:
            t = (t or "").strip()
            if not t:
                return None

            # 去掉常见站点后缀
            t = re.sub(r"\s*[-–—]\s*(萌娘百科|百度百科|维基百科|Wikipedia|哔哩哔哩|bilibili|知乎|网易云音乐|QQ音乐).*?$", "", t, flags=re.I).strip()

            # 进一步：如果仍然包含 “ - ” 分隔，且后半明显像站点名，则保留前半
            if " - " in t:
                left, right = t.split(" - ", 1)
                if re.search(r"(百科|wiki|wikipedia|bilibili|知乎|音乐)", right, re.I):
                    t = left.strip()

            # 去掉包裹性的书名号/引号
            t = t.strip("“”\"'` ")
            t = t.strip()

            if not (2 <= len(t) <= 80):
                return None

            # 必须包含一些“像歌名”的字符（中/日/英/数）
            if not re.search(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]", t):
                return None

            return t

        # 1) 最强优先：带“标识词”的结构化字段
        patterns = [
            r"(?:日语|日語|日文|日文名|日文歌名|日语歌名)\s*[:：]\s*([^\n）\)【\]]{2,80})",
            r"(?:原名|原題|原题)\s*[:：]\s*([^\n）\)【\]]{2,80})",
            r"(?:曲名|歌名|歌曲名)\s*[:：]\s*([^\n）\)【\]]{2,80})",
            r"(?:英文名|英文歌名|英文标题)\s*[:：]\s*([^\n）\)【\]]{2,80})",
            r"(?:罗马字|ローマ字|romanization)\s*[:：]\s*([^\n）\)【\]]{2,80})",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                t = _clean_title(m.group(1))
                if t:
                    return t

        # 2) 次优先：书名号《...》（很多中文资料会这样写歌名/曲名）
        m = re.search(r"《([^》\n]{2,80})》", text)
        if m:
            t = _clean_title(m.group(1))
            if t:
                return t

        # 3) 再次：从 [n] 的“搜索结果标题行”里抽（通常是最可靠的短标题）
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\[\d+\]\s*(.+)$", line)
            if not m:
                continue
            cand = _clean_title(m.group(1))
            if cand:
                return cand

        return None

    def _log(self, title: str, payload: object) -> None:
        """
        统一 debug 日志入口：受 config 控制 + 自动截断。
        """
        if not getattr(self.cfg, "debug_log", False):
            return
        max_chars = int(getattr(self.cfg, "debug_log_max_chars", 1200) or 1200)

        try:
            if isinstance(payload, str):
                msg = _dbg(payload, max_chars=max_chars)
            else:
                msg = _dbg(json.dumps(payload, ensure_ascii=False, indent=2), max_chars=max_chars)
        except Exception:
            msg = _dbg(str(payload), max_chars=max_chars)

        logger.info(f"[ai_assistant.music][debug] {title}\n{msg}")

    def _plan_dump(self, plan: Plan) -> dict:
        return {
            "search_query": plan.search_query,
            "need_web_search": plan.need_web_search,
            "pick_strategy": plan.pick_strategy,
            "platform_hint": plan.platform_hint,
            "song_title": plan.song_title,
            "song_artist": plan.song_artist,
            "web_queries": plan.web_queries,
            "context_style": plan.context_style,
            "domain_hint": plan.domain_hint,
        }

    def _summarize_web_results(self, results: list[dict]) -> list[dict]:
        include_content = bool(getattr(self.cfg, "debug_log_include_web_content", False))
        out: list[dict] = []
        for r in results or []:
            if not isinstance(r, dict):
                continue
            item = {
                "title": (r.get("title") or "").strip(),
                "url": (r.get("url") or "").strip(),
            }
            c = (r.get("content") or "").strip()
            if include_content:
                item["content"] = c
            else:
                item["snippet"] = (c[:240] + ("..." if len(c) > 240 else "")).strip()
            out.append(item)
        return out

    @staticmethod
    def _summarize_songs(songs: list[Song], *, max_items: int = 10) -> list[dict]:
        out: list[dict] = []
        for s in (songs or [])[:max_items]:
            out.append(
                {
                    "id": getattr(s, "id", None),
                    "name": getattr(s, "name", None),
                    "artists": getattr(s, "artists", None),
                    "audio_url": getattr(s, "audio_url", None),
                    "cover_url": getattr(s, "cover_url", None),
                    "note": getattr(s, "note", None),
                }
            )
        return out

    async def _llm_plan(self, raw_request: str, *, extra_context: str | None = None) -> Plan:
        """
        A+B：意图解析 + 语境识别 + 生成 web_queries（慢路径专用）
        - 若带 web_context：必须输出 need_web_search=false（避免循环）
        """
        system = (
            "你是点歌请求解析器。目标：最终能找到一首歌并播放。\n"
            "你必须只输出 JSON（不要解释、不要 Markdown、不要代码块）。\n"
            "\n重要：平台由系统固定选择（统一走网易云），用户即使提到 QQ/酷狗/平台名也不要理会。\n"
            "因此 platform_hint 永远输出 null。\n"
            "\n硬规则（必须遵守）：\n"
            "- 如果提供了 web_context：你必须优先使用其中明确出现的“正式歌名/原名/日文歌名/英文歌名”等信息来填写 song_title 并据此生成 search_query；\n"
            "  不要继续沿用用户输入的模糊片段（如音译/口癖/外号）。\n"
            "\n你必须尽可能抽取：song_title（歌名）和 song_artist（歌手/艺人）。\n"
            "并且：最终 search_query 必须遵循“歌名在前，歌手在后”的拼接规则。\n"
            "例如：\n"
            "- 若 song_title 和 song_artist 都有：search_query = \"{song_title} {song_artist}\"\n"
            "- 若只有 song_title：search_query = \"{song_title}\"\n"
            "- 若只有 song_artist：search_query = \"{song_artist} 热门\"（或 代表作/热门歌曲）\n"
            "\n输出 schema:\n"
            '{"search_query":"string","need_web_search":true|false,"web_queries":["string"],'
            '"pick_strategy":"random|first|best_match","platform_hint":"netease|netease_nodejs|txqq|null",'
            '"song_title":"string|null","song_artist":"string|null",'
            '"context_style":"meme|anime|game|music|general","domain_hint":"bilibili|zhihu|moegirl|wiki|null"}\n'
            "\n要求：\n"
            "- search_query 尽量短（<=40字），用于直接搜歌。\n"
            "- song_title/song_artist 为空时必须输出 null（不要输出空字符串）。\n"
            "- 当请求含不明梗/外语音译/实体不清时 need_web_search=true，否则 false。\n"
            "- 如果你怀疑用户给的是“中文翻译/意译歌名”（而非官方原名），并且你不确定原文歌名/正式歌名：need_web_search 必须为 true，\n"
            "  并在 web_queries 中加入：原名/原曲/日文歌名/英文歌名/音译 等关键词，优先去查到歌曲的原名后再搜歌。\n"
            "- 如果 need_web_search=true：必须提供 web_queries（1~3条，<=60字）。\n"
            "  web_queries 必须至少包含 1 条“指向歌曲”的 query，带关键词之一：BGM/原曲/是什么歌/音译/歌词/配乐/OST。\n"
            "- 如果已经提供 web_context：need_web_search 必须为 false，web_queries 置空或不提供。\n"
            "- context_style：判断语境（meme/anime/game/music/general），用于更聪明的检索。\n"
            "- domain_hint：可选站点倾向（bilibili/zhihu/moegirl/wiki），不确定则 null。\n"
            "- 用户说随便/随机/来一首时 pick_strategy=random。\n"
        )

        user = f"raw_request={raw_request}"
        if extra_context:
            user += "\n\n[web_context]\n" + extra_context

        self._log(
            "llm_plan.request",
            {
                "raw_request": raw_request,
                "has_web_context": bool(extra_context),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

        content, model_name, total_tokens = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=260,
            temperature=self.cfg.llm_temperature,
            top_p=self.cfg.llm_top_p,
        )

        self._log(
            "llm_plan.response",
            {
                "model": model_name,
                "total_tokens": total_tokens,
                "content": content,
            },
        )

        obj = self._safe_parse_json(content)
        self._log("llm_plan.parsed_json", obj)

        if not obj:
            # 兜底：不用联网，直接用原文做搜索
            fallback = Plan(
                search_query=raw_request.strip()[:40] or raw_request,
                need_web_search=False,
                platform_hint=None,
            )
            self._log("llm_plan.fallback_plan", self._plan_dump(fallback))
            return fallback

        def _norm_str(v: object) -> str | None:
            if v is None:
                return None
            if not isinstance(v, str):
                v = str(v)
            v = v.strip()
            return v or None

        song_title = _norm_str(obj.get("song_title"))
        song_artist = _norm_str(obj.get("song_artist"))

        # 有 web_context 时：优先使用联网结果里的“更可靠歌名”（避免 LLM 看漏/被截断误导）
        if extra_context:
            extracted = self._extract_song_title_from_web_context(extra_context)
            if extracted and extracted != song_title:
                self._log(
                    "llm_plan.web_context.song_title.override",
                    {"before": song_title, "after": extracted},
                )
                song_title = extracted

        # 以结构化字段为准，强制“歌名在前歌手在后”
        if song_title and song_artist:
            search_query = f"{song_title} {song_artist}"
        elif song_title:
            search_query = song_title
        elif song_artist:
            search_query = f"{song_artist} 热门"
        else:
            search_query = _norm_str(obj.get("search_query")) or (raw_request.strip()[:40] or raw_request)

        # 如果提供了 web_context，不允许继续要求联网
        need_web_search = False if extra_context else bool(obj.get("need_web_search", False))

        pick_strategy = str(obj.get("pick_strategy") or self.cfg.pick_default).strip().lower()
        if pick_strategy not in {"random", "first", "best_match"}:
            pick_strategy = "random"

        # 平台选择已被系统固定（统一走网易云），不允许用户/LLM 指定
        platform_hint_raw = obj.get("platform_hint")
        if platform_hint_raw is not None:
            self._log("llm_plan.platform_hint.ignored", {"platform_hint": platform_hint_raw})
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

        plan = Plan(
            search_query=search_query,
            need_web_search=need_web_search and self.cfg.allow_web_search,
            pick_strategy=pick_strategy,  # type: ignore[arg-type]
            platform_hint=platform_hint,
            song_title=song_title,
            song_artist=song_artist,
            web_queries=web_queries,
            context_style=context_style,
            domain_hint=domain_hint,
        )

        self._log("llm_plan.normalized_plan", self._plan_dump(plan))
        return plan

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

        self._log(
            "llm_expand_web_queries.request",
            {
                "raw_request": raw_request,
                "context_style": plan.context_style,
                "previous_queries": plan.web_queries,
                "reason": reason,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

        content, model_name, total_tokens = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=180,
            temperature=self.cfg.llm_temperature,
            top_p=self.cfg.llm_top_p,
        )

        self._log(
            "llm_expand_web_queries.response",
            {"model": model_name, "total_tokens": total_tokens, "content": content},
        )

        obj = self._safe_parse_json(content) or {}
        self._log("llm_expand_web_queries.parsed_json", obj)
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

        self._log(
            "tavily.multi_search.plan",
            {
                "queries": queries,
                "include_domains": include_domains,
                "total_max": total_max,
                "per_query": per_query,
            },
        )

        for q in queries:
            try:
                rs = await tavily_search(q, max_results=per_query, include_domains=include_domains)

                # 全量日志：Tavily 原始返回（可能很长，会被 debug_log_max_chars 自动截断）
                self._log(
                    "tavily.search.raw",
                    {
                        "query": q,
                        "include_domains": include_domains,
                        "count": len(rs),
                        "results": rs,
                    },
                )

                # 精简日志：title/url/snippet（默认）
                self._log(
                    "tavily.search.response",
                    {"query": q, "count": len(rs), "results": self._summarize_web_results(rs)},
                )
            except Exception as e:
                logger.warning(f"[ai_assistant.music] Tavily 搜索失败: query={q!r} err={e}")
                self._log("tavily.search.error", {"query": q, "error": str(e)})
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
            self._log("web_search.final", {"raw_request": raw_request, "result": None})
            return None

        # 把“像歌曲页/含原名信息”的结果排到前面，避免 context 截断后丢掉关键信息
        results = self._rank_web_results(results, plan=plan)
        self._log(
            "web_search.ranked",
            {
                "raw_request": raw_request,
                "count": len(results),
                "results": self._summarize_web_results(results),
            },
        )

        formatted = format_search_results(results, max_chars=1200)
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

    async def search_songs(
        self,
        keyword: str,
        *,
        platform_hint: str | None = None,
        extra: str | None = None,
        song_title: str | None = None,
        song_artist: str | None = None,
    ) -> tuple[BaseMusicPlayer, list[Song], bool] | tuple[None, list[Song], bool]:
        await self.ensure_inited()

        # 平台固定：默认走网易云 Web API（ncm）；nodejs 仅作为兜底（可用但可能被限流）
        player = self.get_player(name="netease") or self.get_player(name="netease_nodejs")
        if not player:
            self._log(
                "music.search_songs.no_player",
                {"keyword": keyword, "forced": ["netease", "netease_nodejs"]},
            )
            return None, [], True

        if platform_hint is not None:
            self._log("music.search_songs.platform_hint.ignored", {"platform_hint": platform_hint})

        def _tokens(t: str | None) -> list[str]:
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

        title_tokens = _tokens(song_title)
        artist_tokens = _tokens(song_artist)

        def _relevance_score(s: Song) -> int:
            name = (s.name or "").lower()
            artists = (s.artists or "").lower()
            score = 0
            for tok in title_tokens:
                t = tok.lower()
                if t and t in name:
                    score += 3
            for tok in artist_tokens:
                t = tok.lower()
                if t and t in artists:
                    score += 2
            return score

        def _best_score(rs: list[Song]) -> int:
            if not rs:
                return 0
            if not title_tokens and not artist_tokens:
                return 999  # 不做相关性判断
            return max((_relevance_score(s) for s in rs[:5]), default=0)

        def _is_low_quality(rs: list[Song]) -> bool:
            if not rs:
                return True
            if not title_tokens and not artist_tokens:
                return False
            best = _best_score(rs)
            # 如果完全不命中（best=0），视为“搜歪”
            return best <= 0

        limit = max(1, min(int(self.cfg.candidate_limit), 50))

        # 1) 主查询（由 llm 已强制“歌名在前歌手在后”）
        songs = await player.fetch_songs(keyword=keyword, limit=limit, extra=extra)
        best_score = _best_score(songs)
        lowq = _is_low_quality(songs)
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

        # 2) 兜底（方案B）：仅在“无结果 or 明显不相关”时，再做一次搜索
        # 兜底 query 优先用“纯歌名”，否则用“纯歌手”
        if (not songs) or lowq:
            fallback_query: str | None = None
            if song_title:
                fallback_query = song_title
            elif song_artist:
                fallback_query = song_artist

            if fallback_query and fallback_query != keyword:
                songs2 = await player.fetch_songs(keyword=fallback_query, limit=limit, extra=extra)
                best_score2 = _best_score(songs2)
                lowq2 = _is_low_quality(songs2)
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
                elif songs2 and not songs:
                    # 如果兜底虽然 low_quality，但至少有结果，也优先返回兜底（比完全为空强）
                    final_songs = songs2
                    final_lowq = lowq2

        return player, final_songs, final_lowq

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
                },
            },
        )

        if not raw_request:
            return "你想听什么歌？例如：点歌 给我来一首周杰伦的歌"

        # 先给用户即时反馈
        try:
            await bot.send(event, self.cfg.fast_path_hint)
        except Exception:
            pass

        # 1) LLM 决定是否需要联网 + 生成 plan（含 web_queries / context_style / domain_hint）
        plan = await self._llm_plan(raw_request)
        self._log("music.play.plan1", self._plan_dump(plan))
        web_context_used = False

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
                self._log("music.play.web_search.error", {"error": str(e)})
                context = None

            self._log("music.play.web_search.context", context or "")

            if context:
                plan = await self._llm_plan(raw_request, extra_context=context)
                self._log("music.play.plan2", self._plan_dump(plan))
                web_context_used = True

        # 3) 搜索歌曲（并做“明显搜歪”判定，避免播错歌）
        try:
            player, songs, lowq = await self.search_songs(
                plan.search_query,
                platform_hint=None,
                extra=raw_request,
                song_title=plan.song_title,
                song_artist=plan.song_artist,
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
                "platform_hint": plan.platform_hint,
                "player": None
                if not player
                else {"name": player.platform.name, "display_name": player.platform.display_name},
                "song_count": len(songs),
                "low_quality": lowq,
                "web_context_used": web_context_used,
            },
        )

        if not player:
            return "当前没有可用的音乐平台（网易云不可用）。"

        # 若搜不到或明显不相关：触发一次慢路径纠错（仅一次，避免循环）
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

            # 纠错后仍然“搜歪/无结果”则停止（不播错歌）
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
                return f"搜索结果与“{raw_request}”明显不相关（为避免播错歌已停止）。你可以试试提供原文歌名/更准确关键词。"

        # 4) 选一首
        song = self.pick_song(songs, strategy=plan.pick_strategy, raw_request=raw_request)
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
                    "audio_url": song.audio_url,
                    "cover_url": song.cover_url,
                    "note": song.note,
                },
            },
        )
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
                "song": {
                    "id": song.id,
                    "name": song.name,
                    "artists": song.artists,
                },
            },
        )
        if not ok:
            return "歌曲发送失败（已发送 Now Playing，但歌曲内容未能发出）。"

        return ""
