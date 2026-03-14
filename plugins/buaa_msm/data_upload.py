# plugins/buaa_msm/data_upload.py
"""
兼容层（已重构）：

原本该文件内会直接注册 NoneBot 上传相关命令与消息处理。
重构后已迁移到 `plugins/buaa_msm/handlers/upload.py`，这里仅负责导入以完成注册，
避免重复注册导致的冲突，同时保持外部 import 路径不崩。
"""

from __future__ import annotations

# 导入即完成命令注册
from .handlers import upload as _upload  # noqa: F401

__all__ = []
