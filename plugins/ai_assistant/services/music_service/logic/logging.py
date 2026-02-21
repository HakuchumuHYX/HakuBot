from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nonebot import require
from nonebot.log import logger

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as localstore

from ..types import MusicServiceConfig
from ..core.model import Song
from .models import Plan


def _dbg(text: str, *, max_chars: int) -> str:
    t = (text or "").replace("\r", "").strip()
    if max_chars and len(t) > max_chars:
        return t[:max_chars] + f"...(truncated, total={len(t)})"
    return t


def _safe_filename(s: str, max_len: int = 30) -> str:
    """生成安全的文件名（移除特殊字符）"""
    # 移除或替换不安全字符
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    s = s.strip(". ")
    if len(s) > max_len:
        s = s[:max_len]
    return s or "unnamed"


@dataclass
class LogSession:
    """
    日志会话对象，每个请求独立一个会话。
    使用 dataclass 确保每个会话有独立的状态。
    """
    session_id: str
    raw_request: str
    log_dir: Path
    entries: list[dict] = field(default_factory=list)
    
    def __post_init__(self):
        """初始化时记录会话开始"""
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "title": "_session_start",
            "payload": {
                "session_id": self.session_id,
                "raw_request": self.raw_request,
            },
        })
    
    def add_entry(self, title: str, payload: Any, *, max_chars: int = 0) -> None:
        """添加日志条目"""
        # 处理 payload
        try:
            if isinstance(payload, str):
                processed = _dbg(payload, max_chars=max_chars) if max_chars else payload
            else:
                # 尝试序列化以验证可以写入 JSON
                json.dumps(payload, ensure_ascii=False)
                processed = payload
        except Exception:
            processed = _dbg(str(payload), max_chars=max_chars) if max_chars else str(payload)
        
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "title": title,
            "payload": processed,
        })
    
    def flush(self) -> None:
        """将日志写入 JSON 文件"""
        if not self.entries:
            return
        
        # 记录会话结束
        self.entries.append({
            "timestamp": datetime.now().isoformat(),
            "title": "_session_end",
            "payload": {
                "session_id": self.session_id,
                "entry_count": len(self.entries),
            },
        })
        
        # 生成文件名
        safe_name = _safe_filename(self.raw_request or "unknown")
        filename = f"{self.session_id}_{safe_name}.json"
        log_path = self.log_dir / filename
        
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, ensure_ascii=False, indent=2)
            logger.debug(f"[ai_assistant.music] debug log saved to {log_path}")
        except Exception as e:
            logger.warning(f"[ai_assistant.music] failed to save debug log: {e}")


# 使用 ContextVar 实现请求级别的日志隔离
# 每个异步请求有独立的上下文，不会互相干扰
_current_log_session: ContextVar[LogSession | None] = ContextVar("music_log_session", default=None)


class LoggingMixin:
    """
    统一 debug 日志与"摘要化"工具。
    
    优化：
    1. debug 模式下不再打印到控制台，而是输出到 JSON 文件
    2. 使用 ContextVar 实现请求级别的日志隔离，避免并发问题
    3. 日志文件存储在 data/ai_assistant_music/debug_logs/ 目录下
    4. 每次点歌请求生成一个独立的日志文件，便于追踪和分析
    
    依赖：
      - self.cfg: MusicServiceConfig
    """

    cfg: MusicServiceConfig  # for type checkers

    def _init_log_session(self, raw_request: str) -> None:
        """
        初始化日志会话（每次点歌请求一个新会话）
        
        使用 ContextVar 确保每个异步请求有独立的日志会话，
        避免并发请求之间的日志混淆。
        
        Args:
            raw_request: 用户的原始请求，用于生成日志文件名
        """
        if not getattr(self.cfg, "debug_log", False):
            _current_log_session.set(None)
            return
        
        log_dir = localstore.get_data_dir("ai_assistant_music") / "debug_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        
        # 创建新的日志会话并设置到当前上下文
        session = LogSession(
            session_id=session_id,
            raw_request=raw_request,
            log_dir=log_dir,
        )
        _current_log_session.set(session)

    def _log(self, title: str, payload: Any) -> None:
        """
        记录日志条目（不打印到控制台，存储到当前会话的日志列表）
        
        使用 ContextVar 获取当前请求的日志会话，确保并发安全。
        
        Args:
            title: 日志标题/事件名
            payload: 日志内容（字符串或可序列化对象）
        """
        if not getattr(self.cfg, "debug_log", False):
            return
        
        session = _current_log_session.get()
        if session is None:
            return
        
        max_chars = int(getattr(self.cfg, "debug_log_max_chars", 0) or 0)
        session.add_entry(title, payload, max_chars=max_chars)

    def _flush_log(self) -> None:
        """
        将日志写入 JSON 文件并清理当前会话
        
        日志文件命名格式：{时间戳}_{请求摘要}.json
        存储位置：data/ai_assistant_music/debug_logs/
        """
        if not getattr(self.cfg, "debug_log", False):
            return
        
        session = _current_log_session.get()
        if session is None:
            return
        
        session.flush()
        
        # 清理当前上下文的会话
        _current_log_session.set(None)

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
            # 新增优化字段
            "alternative_queries": plan.alternative_queries,
            "confidence": plan.confidence,
            "parse_reason": plan.parse_reason,
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
