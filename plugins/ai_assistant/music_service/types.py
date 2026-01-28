from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

SelectMode = Literal["text", "single"]
PickStrategy = Literal["random", "first", "best_match"]


@dataclass(slots=True)
class MusicServiceConfig:
    # --- 核心 ---
    default_player_name: str = "网易云音乐"
    nodejs_base_url: str = "https://163api.qijieya.cn"
    song_limit: int = 5
    select_mode: SelectMode = "text"

    # 发送方式优先级
    send_modes: list[str] = field(default_factory=lambda: ["card", "record", "file", "text"])

    # --- 附加功能 ---
    enable_comments: bool = True
    enable_lyrics: bool = False

    # --- 网络 ---
    proxy: Optional[str] = None
    timeout: int = 30
    timeout_recall: bool = True
    allow_web_search: bool = True

    # --- Web Search 增强（ABCD 拉满）---
    # 慢路径 Tavily 搜索的最大重试次数（质量不佳时触发一次“query 扩展+再搜”）
    web_search_retry_times: int = 1
    # 是否启用“站点倾向/白名单”（meme/anime/game/music 场景）
    web_search_domain_bias_enabled: bool = True

    # --- 缓存/数据 ---
    clear_cache: bool = True
    playlist_limit: int = 100

    # --- 网易云热评参数（沿用原点歌插件默认值） ---
    enc_sec_key: str = ""
    enc_params: str = ""

    # --- AI 点歌行为控制 ---
    candidate_limit: int = 5
    pick_default: PickStrategy = "random"

    # 进入慢路径时发一句安抚文案
    slow_path_hint: str = "别急，再等等哦，我去查查相关资料～"
    fast_path_hint: str = "好哦，正在为你寻找……"

    # LLM 解析参数（用于 JSON 提取，尽量低延迟/低随机）
    llm_temperature: Optional[float] = 0.2
    llm_top_p: Optional[float] = None


def real_song_limit(cfg: MusicServiceConfig) -> int:
    return 1 if cfg.select_mode == "single" else cfg.song_limit
