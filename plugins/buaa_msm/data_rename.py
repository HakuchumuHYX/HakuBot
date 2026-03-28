# plugins/buaa_msm/data_rename.py
"""
文件命名工具：根据绑定信息生成目标文件名。
"""

from __future__ import annotations

import re
from datetime import datetime

from .handlers.bind import bind_manager


def make_filename_safe(filename: str) -> str:
    """确保文件名安全，移除或替换非法字符"""
    unsafe_chars = r'[<>:"/\\|?*\x00-\x1f]'
    safe_name = re.sub(unsafe_chars, '_', filename)
    safe_name = safe_name.strip('. ')
    if not safe_name:
        safe_name = 'unnamed'
    if len(safe_name) > 200:
        safe_name = safe_name[:200]
    return safe_name


def generate_target_filename(
    original_filename: str,
    user_id: str,
    *,
    timestamp: datetime | None = None,
) -> str:
    """
    生成目标文件名（不执行落盘重命名）。
    - 若用户已绑定：{user_id}_{bind_content}_{YYYYmmdd_HHMMSS}.bin
    - 若未绑定：保持原文件名（安全化）
    """
    safe_original = make_filename_safe(original_filename)
    bind_content = bind_manager.get(str(user_id))

    if not bind_content:
        return safe_original

    file_ext = ".bin"
    dt = timestamp or datetime.now()
    current_time = dt.strftime("%Y%m%d_%H%M%S")
    safe_bind_content = make_filename_safe(bind_content)

    return f"{user_id}_{safe_bind_content}_{current_time}{file_ext}"
