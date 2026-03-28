# plugins/buaa_msm/__init__.py
"""
BUAA MSM 插件 - Project Sekai MySekai 数据分析工具

结构说明：
- handlers/: NoneBot 命令/消息注册（事件适配层）
- services/: 业务流程编排（解密/解析/缓存/渲染/发送）
- infra/: 基础设施（文件存储/缓存/解密）
- domain/: 领域模型与常量
- renderers/: 纯图片渲染
- parsers/: 数据解析
- resources/: 静态资源与缓存加载
"""

from __future__ import annotations

from nonebot.log import logger

from .infra.storage import load_user_latest_files

# 初始化：加载历史上传文件索引
load_user_latest_files()

# 导入 handlers 完成命令注册
from .handlers import admin as _admin  # noqa: F401
from .handlers import bind as _bind  # noqa: F401
from .handlers import help as _help  # noqa: F401
from .handlers import msr as _msr  # noqa: F401
from .handlers import upload as _upload  # noqa: F401

logger.success("BUAA MSM 插件加载成功！")
