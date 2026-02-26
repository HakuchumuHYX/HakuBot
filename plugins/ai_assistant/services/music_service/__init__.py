from __future__ import annotations

from functools import lru_cache

from nonebot.log import logger

from ...config import plugin_config
from .runtime import MusicService
from .types import MusicServiceConfig


def _build_cfg() -> MusicServiceConfig:
    # Pydantic -> dict -> dataclass
    m = plugin_config.music
    data = m.dict() if hasattr(m, "dict") else dict(m)
    try:
        return MusicServiceConfig(**data)
    except Exception as e:
        logger.error(f"[ai_assistant.music] invalid config, fallback defaults. err={e}")
        return MusicServiceConfig()


@lru_cache(maxsize=1)
def get_music_service() -> MusicService:
    """
    全局单例 MusicService（按当前 ai_assistant config.json 初始化）。
    """
    cfg = _build_cfg()
    return MusicService(cfg)
