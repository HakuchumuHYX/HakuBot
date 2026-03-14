# plugins/buaa_msm/help.py
"""
兼容层（已重构）：

原本该文件内会直接注册 NoneBot 帮助命令。
重构后命令注册已迁移到 `plugins/buaa_msm/handlers/help.py`，这里仅做 re-export，
避免重复注册导致的冲突，同时保持外部 import 路径不崩。

请优先从 `plugins/buaa_msm/handlers/help.py` 使用帮助命令。
"""

from __future__ import annotations

from .handlers.help import (  # noqa: F401
    GROUP_HELP_TEXT,
    PRIVATE_HELP_TEXT,
    group_help_cmd,
    private_help_cmd,
)

__all__ = [
    "GROUP_HELP_TEXT",
    "PRIVATE_HELP_TEXT",
    "private_help_cmd",
    "group_help_cmd",
]
