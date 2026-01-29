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
            name = (getattr(s, "name", None) or "").strip()
            artists = (getattr(s, "artists", None) or "").strip()
            album = (getattr(s, "album", None) or "").strip()
            
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
            "判断规则：\n"
            "1. accept=true的条件（需高度确信）：\n"
            "   - 候选歌曲名与用户请求高度匹配\n"
            "   - 或候选是用户请求的原曲/音译/翻译名\n"
            "   - 泛化请求(如\"随便来首周杰伦的歌\")且候选满足条件也可accept\n"
            "\n"
            "2. accept=false的情况：\n"
            "   - 候选与请求明显不相关\n"
            "   - 无法确定是否匹配（宁可走慢路径）\n"
            "   - 用户请求是梗/外号/场景描述，需要进一步查证\n"
            "\n"
            "3. confidence置信度参考：\n"
            "   - 90-100: 完全匹配，歌名/歌手都对\n"
            "   - 70-89: 高度相关，很可能是对的\n"
            "   - 50-69: 可能相关，但不太确定\n"
            "   - 0-49: 不确定或明显不匹配\n"
            "\n"
            "4. reason示例：\n"
            "   - \"歌名和歌手完全匹配\"\n"
            "   - \"候选1是用户请求的日文原名\"\n"
            "   - \"用户请求是梗，需要查证具体歌曲\"\n"
            "   - \"候选与请求无明显关联\"\n"
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
