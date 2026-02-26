from __future__ import annotations

import json
import re
from typing import Optional

from nonebot.log import logger

from ...chat_service import call_chat_completion
from ..types import MusicServiceConfig
from .models import Plan


# 需要联网的高频场景关键词（用于辅助判断）
_WEB_SEARCH_HINT_KEYWORDS = [
    # 梗/二次元相关
    "梗", "出处", "原曲", "bgm", "ost", "配乐", "插曲", "片头曲", "片尾曲", "op", "ed",
    # 音译/外语相关
    "音译", "空耳", "谐音", "罗马音", "假名", "日语", "日文", "英文", "韩语", "韩文",
    # 翻译/意译相关
    "翻译", "意译", "中文名", "原名", "正式名", "官方名",
    # 不确定性表达
    "好像", "大概", "可能", "应该是", "叫什么", "是什么歌", "什么歌",
    # 场景描述
    "那首", "那个", "以前听过", "之前的", "某个", "某首",
    # 游戏/动漫/视频相关
    "游戏", "动漫", "番剧", "电影", "视频", "直播", "主播", "up主",
]


class LLMPlanMixin:
    """
    LLM 相关逻辑：
    - JSON 安全解析
    - 点歌意图抽取（歌名/歌手/是否需要联网/生成 web_queries）
    - 低质量 web search 时的 query 扩展
    - 从 web_context 提取更可靠的正式歌名
    - 支持多变体输出和置信度评估
    依赖：
      - self.cfg: MusicServiceConfig
      - self._log / self._plan_dump: LoggingMixin
    """

    cfg: MusicServiceConfig  # for type checkers

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

    def _normalize_alternative_queries(self, raw_q: object) -> Optional[list[str]]:
        """规范化备选搜索词列表"""
        if not isinstance(raw_q, list):
            return None
        max_count = getattr(self.cfg, "max_alternative_queries", 3)
        cleaned: list[str] = []
        for q in raw_q:
            if not isinstance(q, str):
                continue
            q = q.strip()
            if not q:
                continue
            if len(q) > 50:
                q = q[:50].strip()
            if q not in cleaned:
                cleaned.append(q)
        return cleaned[:max_count] if cleaned else None

    def _check_web_search_hints(self, raw_request: str) -> bool:
        """检查用户请求是否包含需要联网的关键词"""
        text = raw_request.lower()
        for kw in _WEB_SEARCH_HINT_KEYWORDS:
            if kw.lower() in text:
                return True
        return False

    # 不应从 web_context 提取的标题模式（这些是目录/列表页，不是具体歌名）
    _WEB_CONTEXT_TITLE_BLACKLIST_PATTERNS = [
        r"演唱歌曲",
        r"收[录錄]歌曲",
        r"歌曲[列目]",
        r"[目歌]录",
        r"合[集辑輯]",
        r"大全",
        r"所有歌曲",
        r"全部歌曲",
        r"歌曲列表",
        r"discography",
        r"song\s*list",
        r"music\s*list",
    ]

    @staticmethod
    def _extract_song_title_from_web_context(web_context: str) -> str | None:
        """
        从 web_context 里提取"更可靠的正式歌名"，用于覆盖 LLM 规划时的 song_title。
        
        注意：此函数非常保守，只在高置信度时才返回结果。
        它会过滤掉目录页、列表页等非具体歌名的标题。
        """
        if not web_context:
            return None

        text = web_context.strip()

        def _is_blacklisted(t: str) -> bool:
            """检查标题是否匹配黑名单模式（目录/列表页等）"""
            t_lower = t.lower()
            for pattern in LLMPlanMixin._WEB_CONTEXT_TITLE_BLACKLIST_PATTERNS:
                if re.search(pattern, t_lower, re.I):
                    return True
            return False

        def _clean_title(t: str) -> str | None:
            t = (t or "").strip()
            if not t:
                return None

            # 去掉常见站点后缀
            t = re.sub(
                r"\s*[-–—]\s*(萌娘百科|百度百科|维基百科|Wikipedia|哔哩哔哩|bilibili|知乎|网易云音乐|QQ音乐).*?$",
                "",
                t,
                flags=re.I,
            ).strip()

            # 进一步：如果仍然包含 " - " 分隔，且后半明显像站点名，则保留前半
            if " - " in t:
                left, right = t.split(" - ", 1)
                if re.search(r"(百科|wiki|wikipedia|bilibili|知乎|音乐)", right, re.I):
                    t = left.strip()

            # 去掉包裹性的书名号/引号
            t = t.strip('"""\"\' ')
            t = t.strip()

            if not (2 <= len(t) <= 80):
                return None

            # 必须包含一些"像歌名"的字符（中/日/英/数）
            if not re.search(r"[\u3040-\u30ff\u4e00-\u9fffA-Za-z0-9]", t):
                return None
            
            # 检查是否匹配黑名单（目录页、列表页等）
            if _is_blacklisted(t):
                return None

            return t

        # 1) 最强优先：带"标识词"的结构化字段
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

        # 2) 次优先：书名号《...》
        m = re.search(r"《([^》\n]{2,80})》", text)
        if m:
            t = _clean_title(m.group(1))
            if t:
                return t

        # 3) 再次：从 [n] 的"搜索结果标题行"里抽
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

    async def _llm_plan(self, raw_request: str, *, extra_context: str | None = None) -> Plan:
        """
        A+B: 意图解析 + 语境识别 + 生成 web_queries (慢路径专用)
        - 若带 web_context: 必须输出 need_web_search=false (避免循环)
        - 支持多变体输出和置信度评估
        """
        # 预检查：是否包含需要联网的关键词
        has_web_hints = self._check_web_search_hints(raw_request)

        system = (
            "你是点歌请求解析器。目标：解析用户的点歌意图，提取歌曲信息，最终能找到一首歌并播放。\n"
            "只输出JSON，不要解释、不要Markdown、不要代码块。\n"
            "\n"
            "## 输出Schema\n"
            "{\n"
            '  "search_query": "string",           // 主搜索词(<=40字)，用于直接搜歌\n'
            '  "song_title": "string|null",        // 歌曲名(如能识别)\n'
            '  "song_artist": "string|null",       // 歌手名(如能识别)\n'
            '  "alternative_queries": ["string"],  // 备选搜索词(原名/音译/变体/英文名等)，最多3个\n'
            '  "need_web_search": true|false,      // 是否需要联网查找歌曲信息\n'
            '  "web_queries": ["string"],          // 联网搜索词(1-3个，需联网时必填)\n'
            '  "confidence": 0-100,                // 你对解析结果的置信度\n'
            '  "parse_reason": "string",           // 简短说明你的解析理由\n'
            '  "pick_strategy": "random|first|best_match",\n'
            '  "context_style": "meme|anime|game|music|general",\n'
            '  "domain_hint": "bilibili|zhihu|moegirl|wiki|null",\n'
            '  "platform_hint": null               // 固定为null(系统统一走网易云)\n'
            "}\n"
            "\n"
            "## 核心规则\n"
            "1. search_query格式：歌名在前，歌手在后。例如：\n"
            '   - 有歌名有歌手: "千本樱 初音ミク"\n'
            '   - 只有歌名: "千本樱"\n'
            '   - 只有歌手: "周杰伦 热门"\n'
            "\n"
            "2. need_web_search判断（满足任一则为true）：\n"
            "   - 用户用了梗/外号/音译/空耳而非正式歌名\n"
            "   - 用户描述的是场景(如\"那个xxx的BGM\")\n"
            "   - 你不确定用户说的是什么歌\n"
            "   - 用户用了中文翻译名但你不确定原名\n"
            "   - 请求包含不明确的指代(那首、那个、之前的)\n"
            "\n"
            "3. alternative_queries（备选搜索词）：\n"
            "   - 如果歌名可能有多个版本/写法，列出备选\n"
            "   - 包括：原名、音译、英文名、别名、常见错写\n"
            "   - 例如用户说\"恋爱循环\"，备选可加\"恋愛サーキュレーション\"\n"
            "\n"
            "4. confidence置信度参考：\n"
            "   - 90-100: 非常确定(用户给了完整歌名+歌手)\n"
            "   - 70-89: 比较确定(用户给了歌名或歌手)\n"
            "   - 50-69: 不太确定(可能需要联网)\n"
            "   - 0-49: 很不确定(强烈建议联网)\n"
            "\n"
            "5. 若已提供web_context：\n"
            "   - need_web_search必须为false\n"
            "   - 【重要】必须仔细阅读web_context，从中提取正式歌名/原名\n"
            "   - 如果web_context中包含日文歌名（如\"しゅきしゅきソング\"），优先使用日文原名\n"
            "   - 如果web_context中说明了用户请求的梗/音译对应哪首歌，必须使用那首歌的正式名称\n"
            "   - 例如：用户说\"shukishuki\"，web_context说是\"自己肯定感爆上げ↑↑しゅきしゅきソング\"，则song_title应为该正式名称\n"
            "\n"
            "## Few-shot示例\n"
            "\n"
            "### 示例1：标准点歌\n"
            "输入: 来一首周杰伦的晴天\n"
            "输出: {\n"
            '  "search_query": "晴天 周杰伦",\n'
            '  "song_title": "晴天",\n'
            '  "song_artist": "周杰伦",\n'
            '  "alternative_queries": [],\n'
            '  "need_web_search": false,\n'
            '  "web_queries": [],\n'
            '  "confidence": 95,\n'
            '  "parse_reason": "用户明确指定了歌名和歌手",\n'
            '  "pick_strategy": "first",\n'
            '  "context_style": "music",\n'
            '  "domain_hint": null,\n'
            '  "platform_hint": null\n'
            "}\n"
            "\n"
            "### 示例2：梗/音译\n"
            "输入: 播放雪花飘飘北风萧萧\n"
            "输出: {\n"
            '  "search_query": "一剪梅 费玉清",\n'
            '  "song_title": "一剪梅",\n'
            '  "song_artist": "费玉清",\n'
            '  "alternative_queries": ["雪花飘飘北风萧萧", "Yi Jian Mei"],\n'
            '  "need_web_search": false,\n'
            '  "web_queries": [],\n'
            '  "confidence": 85,\n'
            '  "parse_reason": "这是一剪梅的经典歌词，网络热梗",\n'
            '  "pick_strategy": "first",\n'
            '  "context_style": "meme",\n'
            '  "domain_hint": null,\n'
            '  "platform_hint": null\n'
            "}\n"
            "\n"
            "### 示例3：需要联网的场景\n"
            "输入: 那个拔剑神曲是什么\n"
            "输出: {\n"
            '  "search_query": "拔剑神曲",\n'
            '  "song_title": null,\n'
            '  "song_artist": null,\n'
            '  "alternative_queries": ["拔剑BGM", "拔剑配乐"],\n'
            '  "need_web_search": true,\n'
            '  "web_queries": ["拔剑神曲 是什么歌 BGM", "拔剑神曲 原曲"],\n'
            '  "confidence": 30,\n'
            '  "parse_reason": "用户描述的是一个梗，需要联网查找对应歌曲",\n'
            '  "pick_strategy": "first",\n'
            '  "context_style": "meme",\n'
            '  "domain_hint": "bilibili",\n'
            '  "platform_hint": null\n'
            "}\n"
            "\n"
            "### 示例4：日文歌/翻译名\n"
            "输入: 恋爱循环\n"
            "输出: {\n"
            '  "search_query": "恋愛サーキュレーション",\n'
            '  "song_title": "恋愛サーキュレーション",\n'
            '  "song_artist": "花澤香菜",\n'
            '  "alternative_queries": ["恋爱循环", "Renai Circulation", "花泽香菜 恋爱循环"],\n'
            '  "need_web_search": false,\n'
            '  "web_queries": [],\n'
            '  "confidence": 80,\n'
            '  "parse_reason": "恋爱循环是《化物语》插曲的中文名，原名恋愛サーキュレーション",\n'
            '  "pick_strategy": "first",\n'
            '  "context_style": "anime",\n'
            '  "domain_hint": null,\n'
            '  "platform_hint": null\n'
            "}\n"
            "\n"
            "### 示例5：模糊请求\n"
            "输入: 来点轻音乐\n"
            "输出: {\n"
            '  "search_query": "轻音乐 纯音乐",\n'
            '  "song_title": null,\n'
            '  "song_artist": null,\n'
            '  "alternative_queries": ["钢琴曲", "轻音乐合集"],\n'
            '  "need_web_search": false,\n'
            '  "web_queries": [],\n'
            '  "confidence": 60,\n'
            '  "parse_reason": "用户想听轻音乐类型，未指定具体歌曲",\n'
            '  "pick_strategy": "random",\n'
            '  "context_style": "music",\n'
            '  "domain_hint": null,\n'
            '  "platform_hint": null\n'
            "}\n"
            "\n"
            "## 中文意译/逐字翻译识别规则（重要技巧）\n"
            "当用户使用疑似\"逐字翻译\"的中文表达指代外语歌名时：\n"
            "1. 识别模式：用户用中文词汇逐字翻译外语歌名\n"
            "   - 例：月说也许 → 月=Luna/Moon 说=Say 也许=Maybe → 可能指\"Maybe\"或\"Luna Say Maybe\"\n"
            "   - 例：星之卡比 → 星=Star/Hoshi → Kirby / 星のカービィ\n"
            "2. 常见对照词（中文→英/日）：\n"
            "   - 月→Luna/Moon/つき  星→Star/Hoshi  空→Sky/Sora\n"
            "   - 花→Flower/Hana  爱→Love/Ai  心→Heart/Kokoro\n"
            "   - 梦→Dream/Yume  说→Say  也许→Maybe/Perhaps\n"
            "   - 永远→Forever/Eternal  夜→Night/Yoru  光→Light/Hikari\n"
            "3. 处理策略：\n"
            "   - 在alternative_queries中同时包含：用户原文、可能的英文组合、可能的日文罗马音\n"
            "   - 设置较低的confidence(40-60)\n"
            "   - need_web_search=true，让联网搜索帮助确认\n"
            "   - web_queries中包含多种变体：原文+\"是什么歌\"、英文组合+\"原曲\"等\n"
            "4. 在parse_reason中说明你的意译推理过程\n"
        )

        user = f"raw_request={raw_request}"
        if extra_context:
            user += "\n\n[web_context]\n" + extra_context

        self._log(
            "llm_plan.request",
            {
                "raw_request": raw_request,
                "has_web_context": bool(extra_context),
                "has_web_hints": has_web_hints,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

        content, model_name, total_tokens = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=4096,  # 增加token限制以容纳新字段
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
                need_web_search=has_web_hints and self.cfg.allow_web_search,  # 根据关键词提示判断
                pick_strategy=self.cfg.pick_default,
                platform_hint=None,
                confidence=30,
                parse_reason="JSON解析失败，使用原始输入",
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

        # 先解析置信度，以便后续判断
        confidence = 50
        try:
            confidence = int(obj.get("confidence", 50))
            confidence = max(0, min(100, confidence))
        except Exception:
            confidence = 50

        # 有 web_context 时：优先信任 LLM 解析结果
        # 只在 LLM 没解析出歌名，或置信度很低时，才尝试从 web_context 提取覆盖
        if extra_context:
            # 只在 LLM 没解析出歌名，或置信度非常低时，才尝试覆盖
            if not song_title or confidence < 50:
                extracted = self._extract_song_title_from_web_context(extra_context)
                if extracted and extracted != song_title:
                    self._log(
                        "llm_plan.web_context.song_title.override",
                        {"before": song_title, "after": extracted, "reason": "LLM未解析出歌名或置信度过低"},
                    )
                    song_title = extracted
            else:
                # 记录跳过覆盖的原因
                self._log(
                    "llm_plan.web_context.song_title.skip_override",
                    {"song_title": song_title, "confidence": confidence, "reason": "LLM已成功解析且置信度足够"},
                )

        # 以结构化字段为准，强制"歌名在前歌手在后"
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
        
        # 如果LLM没判断需要联网，但包含关键词提示，且置信度较低，也触发联网
        if not extra_context and not need_web_search and has_web_hints and confidence < 70:
            need_web_search = True
            self._log(
                "llm_plan.web_search.hint_triggered",
                {"confidence": confidence, "has_web_hints": has_web_hints},
            )

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
        
        # 解析备选搜索词
        alternative_queries = self._normalize_alternative_queries(obj.get("alternative_queries"))
        
        # 解析理由
        parse_reason = _norm_str(obj.get("parse_reason"))

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
            alternative_queries=alternative_queries,
            confidence=confidence,
            parse_reason=parse_reason,
        )

        self._log("llm_plan.normalized_plan", self._plan_dump(plan))
        return plan

    async def _llm_expand_web_queries(self, raw_request: str, *, plan: Plan, reason: str) -> list[str]:
        """
        D：结果质量差时，让 LLM 再生成一组更强的 web_queries（偏向"找到歌曲/BGM/原曲"）。
        支持意译识别和多变体生成。
        """
        system = (
            "你是联网检索query生成器。目标：为了找到\"对应的歌曲/BGM/原曲/歌词/音译\"。\n"
            "只输出JSON：{ \"web_queries\": [\"...\"] }\n"
            "\n"
            "## 核心规则\n"
            "- 输出1~3条query，每条<=60字\n"
            "- 必须至少1条包含关键词之一：BGM/原曲/是什么歌/音译/歌词/配乐/OST\n"
            "- 若语境是meme/anime/game，请加入：梗/出处/口癖/台词之一\n"
            "- 避免泛问法（不要只写\"什么是X\"），要带语境限定\n"
            "- 尝试不同的搜索角度（歌名/歌手/作品名/歌词片段）\n"
            "\n"
            "## 意译/翻译名识别（重要！）\n"
            "如果用户请求中包含疑似\"逐字翻译\"的中文表达：\n"
            "1. 识别并尝试还原可能的外语原名：\n"
            "   - 月→Luna/Moon  星→Star  空→Sky  花→Flower\n"
            "   - 爱→Love  心→Heart  梦→Dream  说→Say\n"
            "   - 也许→Maybe  永远→Forever  夜→Night  光→Light\n"
            "2. 生成多种变体query：\n"
            "   - 原中文 + \"是什么歌\"\n"
            "   - 可能的英文组合 + \"原曲\"\n"
            "   - 歌手名（如已知）+ 可能的英文歌名\n"
            "3. 示例：\n"
            "   - 用户说\"月说也许\" → 生成 [\"月说也许 是什么歌\", \"Luna Say Maybe 原曲\", \"Maybe 歌曲\"]\n"
            "   - 用户说\"星之梦\" → 生成 [\"星之梦 是什么歌\", \"Star Dream 原曲\", \"Hoshi no Yume 歌曲\"]\n"
        )
        user = (
            f"raw_request={raw_request}\n"
            f"song_title={plan.song_title}\n"
            f"song_artist={plan.song_artist}\n"
            f"alternative_queries={plan.alternative_queries}\n"
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

    async def _llm_verify_relevance(
        self,
        raw_request: str,
        candidates: list[dict],
        *,
        song_artist: str | None = None,
        parse_reason: str | None = None,
    ) -> tuple[bool, int | None, str]:
        """
        LLM相关性二次验证：判断搜索结果候选是否与用户请求匹配。
        
        新增参数：
        - song_artist: LLM解析出的歌手/组合名（用于提供上下文）
        - parse_reason: LLM解析的理由（用于提供上下文）
        
        返回：(is_relevant, best_index_1based|None, reason)
        """
        if not candidates:
            return False, None, "无候选结果"

        # 格式化候选列表
        lines: list[str] = []
        for i, c in enumerate(candidates[:5], start=1):
            name = c.get("name", "").strip()
            artists = c.get("artists", "").strip()
            if name or artists:
                lines.append(f"[{i}] {name} - {artists}".strip(" -"))
        candidates_text = "\n".join(lines) if lines else "（无候选）"

        system = (
            "你是点歌结果相关性判断器。判断搜索结果是否与用户点歌请求匹配。\n"
            "只输出JSON，不要解释。\n"
            "\n"
            "输出格式：\n"
            '{"relevant": true|false, "best_index": 1-5|null, "reason": "string"}\n'
            "\n"
            "规则：\n"
            "- relevant为true：候选中有歌曲与用户请求高度相关\n"
            "- relevant为false：候选与用户请求明显不相关或无法确定\n"
            "- best_index：如果relevant为true，指出最匹配的候选序号(1-5)\n"
            "- reason：简短说明判断理由\n"
            "\n"
            "## 跨语言匹配规则（重要）\n"
            "- 中文名与日文名可能对应同一事物（如\"25时\"=\"25時\"）\n"
            "- 简体中文与日文汉字可能是同一个词（如\"时\"=\"時\"）\n"
            "- 组合/乐队名可能有多种写法（如\"25时，在Nightcord。\"=\"25時、ナイトコードで。\"）\n"
            "- 如果候选歌手名包含与用户请求相同的数字+相似的汉字，很可能是匹配的\n"
            "\n"
            "注意：\n"
            "- 考虑音译/别名/翻译名的对应关系\n"
            "- 用户可能用梗/外号/缩写来指代歌曲或歌手\n"
            "- 如果候选歌手与LLM解析的歌手在语义上等价，应认为匹配\n"
        )

        # 构建用户消息，包含LLM解析上下文
        user_parts = [f"用户请求: {raw_request}"]
        
        if song_artist:
            user_parts.append(f"LLM解析的目标歌手/组合: {song_artist}")
        if parse_reason:
            user_parts.append(f"解析理由: {parse_reason}")
        
        user_parts.append(f"\n搜索结果:\n{candidates_text}")
        
        user = "\n".join(user_parts)

        self._log(
            "llm_verify_relevance.request",
            {
                "raw_request": raw_request,
                "candidates": candidates_text,
            },
        )

        content, model_name, total_tokens = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=150,
            temperature=0.1,
            top_p=0.9,
        )

        self._log(
            "llm_verify_relevance.response",
            {"model": model_name, "total_tokens": total_tokens, "content": content},
        )

        obj = self._safe_parse_json(content) or {}
        relevant = bool(obj.get("relevant", False))
        reason = str(obj.get("reason", "")).strip() or "未知"

        best_index: int | None = None
        try:
            v = obj.get("best_index")
            if v is not None:
                best_index = int(v)
                if best_index < 1 or best_index > min(len(candidates), 5):
                    best_index = None
        except Exception:
            best_index = None

        self._log(
            "llm_verify_relevance.result",
            {"relevant": relevant, "best_index": best_index, "reason": reason},
        )

        return relevant, best_index, reason
