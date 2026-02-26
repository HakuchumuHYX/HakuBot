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
    # 慢路径 Tavily 搜索的最大重试次数（质量不佳时触发一次"query 扩展+再搜"）
    web_search_retry_times: int = 1
    # 是否启用"站点倾向/白名单"（meme/anime/game/music 场景）
    web_search_domain_bias_enabled: bool = True

    # --- debug 日志（用于排查"抽象点歌"问题） ---
    # 是否打印详细 debug 日志（LLM 原始输出、Tavily 结果摘要、候选列表等）
    debug_log: bool = False
    # 单条日志最大长度（超出截断）
    debug_log_max_chars: int = 1200
    # 是否包含 Tavily 的 content 全文（默认 false，只打 title/url/snippet）
    debug_log_include_web_content: bool = False

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

    # --- 优化相关配置 ---
    # 快路径置信度阈值（LLM返回的confidence低于此值时走慢路径）
    fast_path_confidence_threshold: int = 70
    # 是否启用LLM相关性二次验证（搜索结果低质量时）
    enable_llm_relevance_check: bool = True
    # 是否启用多变体搜索（同时用原名/音译/变体搜索）
    enable_alternative_queries: bool = True
    # 备选query最大数量
    max_alternative_queries: int = 3
    # LLM解析超时降级：超时后直接用原始输入搜索
    llm_timeout_fallback: bool = True
    # LLM超时时间（秒）
    llm_timeout_seconds: float = 15.0
    # 并行执行：快路径搜索同时启动LLM解析
    enable_parallel_fast_path: bool = True


def real_song_limit(cfg: MusicServiceConfig) -> int:
    return 1 if cfg.select_mode == "single" else cfg.song_limit
