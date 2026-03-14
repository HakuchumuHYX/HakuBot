# plugins/buaa_msm/renderers/__init__.py
"""
渲染层（renderers）

说明：
- 存放“输入结构化数据 -> 输出图片 bytes/图片对象”的纯渲染逻辑
- 不应包含 NoneBot 事件/消息发送逻辑（那些应在 handlers/services）
"""
