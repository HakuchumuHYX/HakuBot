# plugins/buaa_msm/__init__.py
"""
BUAA MSM 插件 - Project Sekai MySekai 数据分析工具

结构说明：
- handlers/: NoneBot 命令/消息注册（事件适配层）
- services/: 业务流程编排（解密/解析/缓存/渲染/发送）
- 帮助/绑定命令保持不变
"""

from __future__ import annotations

from nonebot.log import logger

from .infra.storage import load_user_latest_files

# 初始化：加载历史上传文件索引（替代旧 data_manage.py import 副作用）
load_user_latest_files()

# 导入 handlers 完成命令注册
from .handlers import admin as _admin  # noqa: F401
from .handlers import bind as _bind  # noqa: F401
from .handlers import help as _help  # noqa: F401
from .handlers import msr as _msr  # noqa: F401
from .handlers import upload as _upload  # noqa: F401

from .services.processing_guard import is_processing, set_processing  # noqa: F401

__all__ = [
    "is_processing",
    "set_processing",
]

logger.success("BUAA MSM 插件加载成功！（已重构：handlers/services 分层）")
