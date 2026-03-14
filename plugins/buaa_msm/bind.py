# plugins/buaa_msm/bind.py
"""
兼容层（已重构）：

原本该文件内会直接注册 NoneBot 命令。
重构后命令注册已迁移到 `plugins/buaa_msm/handlers/bind.py`，这里仅做 re-export，
避免重复注册导致的冲突，同时保持外部 import 路径不崩。

请优先从 `plugins/buaa_msm/handlers/bind.py` 使用绑定命令。
"""

from __future__ import annotations

from .handlers.bind import (  # noqa: F401
    BindManager,
    bind_cmd,
    bind_manager,
    query_bind,
    unbind_cmd,
    view_all_binds,
)

__all__ = [
    "BindManager",
    "bind_manager",
    "bind_cmd",
    "query_bind",
    "unbind_cmd",
    "view_all_binds",
]
