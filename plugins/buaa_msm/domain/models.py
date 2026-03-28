# plugins/buaa_msm/domain/models.py
"""
领域数据模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

ParsedMaps = dict[str, list]


@dataclass(frozen=True, slots=True)
class UserDataContext:
    """解密 + 解析后的用户上下文（用于渲染/分析）"""
    user_id: str
    decrypted_data: dict[str, Any]
    parsed_maps: ParsedMaps
    latest_file_path: Path
    user_output_dir: Path


@dataclass(frozen=True, slots=True)
class UserDataResult:
    ok: bool
    ctx: UserDataContext | None = None
    error: str | None = None
