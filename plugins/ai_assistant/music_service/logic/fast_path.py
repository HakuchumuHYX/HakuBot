from __future__ import annotations

from typing import Optional

from ...service import call_chat_completion
from ..core.model import Song
from ..core.platform import BaseMusicPlayer
from ..types import MusicServiceConfig


class FastPathMixin:
    """
    "网易云直搜 -> LLM 快速命中判定"快路径。

    依赖：
      - self.cfg: MusicServiceConfig
      - await self.ensure_inited()
      - self.get_player(...)
      - self._log / self._summarize_songs / self._safe_parse_json
    """

    cfg: MusicServiceConfig  # for type checkers

    async def _get_netease_player(self) -> BaseMusicPlayer | None:
        """
        平台固定：优先网易云 Web API（netease），其次 NodeJS 兜底（netease_nodejs）。
        """
        await self.ensure_inited()
        return self.get_player(name="netease") or self.get_player(name="netease_nodejs")

    async def _netease_presearch(self, raw_request: str) -> tuple[BaseMusicPlayer | None, list[Song]]:
        """
        按用户原始输入直接请求网易云，作为"最快路径"的候选集。
        """
        player = await self._get_netease_player()
        if not player:
            return None, []

        limit = max(1, min(int(self.cfg.candidate_limit), 10))
        try:
            songs = await player.fetch_songs(keyword=raw_request, limit=limit, extra=raw_request)
        except Exception as e:
            self._log(
                "music.presearch.error",
                {"raw_request": raw_request, "error": str(e), "player": player.platform.name},
            )
            return player, []

        self._log(
            "music.presearch.result",
            {
                "raw_request": raw_request,
                "player": {"name": player.platform.name, "display_name": player.platform.display_name},
                "limit": limit,
                "count": len(songs),
                "songs": self._summarize_songs(songs, max_items=limit),
            },
        )
        return player, songs

    @staticmethod
    def _format_candidate_songs_for_llm(songs: list[Song], *, max_items: int = 5) -> str:
        """
        格式化候选歌曲供LLM判断，增加更多信息（专辑名等）
        """
        if not songs:
            return "（无候选）"
        lines: list[str] = []
        for i, s in enumerate((songs or [])[:max_items], start=1):
            name = (s.name or "").strip()
            artists = (s.artists or "").strip()
            album = (s.album or "").strip()
            
            if not name and not artists:
                continue
            
            # 基础信息
            line = f"[{i}] {name}"
            if artists:
                line += f" - {artists}"
            if album:
                line += f" (专辑: {album})"
            
            lines.append(line.strip())
        return "\n".join(lines) if lines else "（无候选）"

    async def _llm_fast_gate(self, raw_request: str, songs: list[Song]) -> tuple[bool, int | None, int, str]:
        """
        给 LLM 一次"超轻量判断"：候选是否与请求相似；若相似，直接从候选中挑一个索引。
        返回：(accept, pick_index_1based|None, confidence, reason)
        
        新增：
        - confidence: 置信度(0-100)，低于阈值走慢路径
        - reason: 判断理由说明
        """
        candidates_text = self._format_candidate_songs_for_llm(songs, max_items=5)

        system = (
            "你是点歌\"快速命中判定器\"。判断网易云搜索结果是否已包含用户想要的歌曲。\n"
            "只输出JSON，不要解释/Markdown/代码块。\n\n"
            "输出格式：\n"
            "{\n"
            '  "accept": true|false,     // 候选中是否有匹配的歌曲\n'
            '  "pick_index": 1-5|null,   // 如果accept为true，选择哪首(1-based序号)\n'
            '  "confidence": 0-100,      // 你对这个判断的置信度\n'
            '  "reason": "string"        // 简短说明判断理由\n'
            "}\n\n"
            "## 判断规则\n"
            "1. accept=true的严格条件（必须高度确信）：\n"
            "   - 候选歌曲名与用户请求的歌名有明确的文字匹配\n"
            "   - 或候选歌手名与用户请求的歌手/组合名有明确的文字匹配\n"
            "   - 泛化请求(如\"随便来首周杰伦的歌\")时，候选歌手名必须包含\"周杰伦\"\n"
            "\n"
            "2. accept=false的情况（宁可误否不要误accept）：\n"
            "   - 候选歌手名与用户请求的歌手/组合名没有文字上的重叠\n"
            "   - 需要进行任何\"推测\"或\"假设\"才能建立关联\n"
            "   - 用户请求是梗/外号/缩写，而候选中没有直接对应\n"
            "   - 不确定时一律返回accept=false\n"
            "\n"
            "3. confidence置信度参考：\n"
            "   - 90-100: 歌名和歌手都有明确的文字匹配\n"
            "   - 70-89: 歌名或歌手有明确匹配\n"
            "   - 50-69: 可能相关，但文字匹配不明确\n"
            "   - 0-49: 不确定或无法匹配\n"
            "\n"
            "## 重要约束（必须遵守）\n"
            "- 你只能基于候选歌曲中【明确显示】的歌名和歌手信息做判断\n"
            "- 【禁止】假设任何歌手\"可能是\"某个厂牌/公司/组合的成员\n"
            "- 【禁止】基于你对音乐行业的知识推测候选歌手与用户请求的关系\n"
            "- 如果候选歌手名中没有明确包含用户请求的关键词，必须返回accept=false\n"
            "- 例如：用户说\"25h\"或\"25时\"，候选歌手必须包含\"25\"相关文字才能accept\n"
            "\n"
            "## reason示例\n"
            "- 正确：\"候选1的歌手名'25時、ナイトコードで。'包含'25'，与用户请求'25时'匹配\"\n"
            "- 正确：\"候选与请求无文字匹配，需走慢路径\"\n"
            "- 错误：\"候选歌手可能是某厂牌成员\"（禁止这种推测）\n"
        )

        user = f"用户请求: {raw_request}\n\n候选歌曲:\n{candidates_text}"

        self._log(
            "music.fast_gate.request",
            {
                "raw_request": raw_request,
                "candidates": candidates_text,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

        content, model_name, total_tokens = await call_chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=150,
            temperature=0.1,
            top_p=0.9,
        )

        self._log(
            "music.fast_gate.response",
            {"model": model_name, "total_tokens": total_tokens, "content": content},
        )

        obj = self._safe_parse_json(content) or {}
        accept = bool(obj.get("accept", False))
        reason = str(obj.get("reason", "")).strip() or "未说明"

        # 解析置信度
        confidence = 50
        try:
            confidence = int(obj.get("confidence", 50))
            confidence = max(0, min(100, confidence))
        except Exception:
            confidence = 50

        # 解析pick_index
        pick_index: int | None = None
        try:
            v = obj.get("pick_index", None)
            if v is not None:
                pick_index = int(v)
        except Exception:
            pick_index = None

        if pick_index is not None:
            if pick_index < 1 or pick_index > min(len(songs), 5):
                pick_index = None

        # 置信度低于阈值时，即使accept=true也不采纳
        threshold = getattr(self.cfg, "fast_path_confidence_threshold", 70)
        if accept and confidence < threshold:
            self._log(
                "music.fast_gate.confidence_reject",
                {
                    "accept": accept,
                    "confidence": confidence,
                    "threshold": threshold,
                    "reason": reason,
                },
            )
            accept = False
            reason = f"置信度{confidence}低于阈值{threshold}，走慢路径"

        self._log(
            "music.fast_gate.parsed",
            {
                "accept": accept,
                "pick_index": pick_index,
                "confidence": confidence,
                "reason": reason,
            },
        )
        return accept, pick_index, confidence, reason
