"""
Service layer for buaa_msm.

说明：
- services 负责业务流程编排（解密/解析/缓存/调用渲染器/发送结果/fallback 等）
- handlers 只做 NoneBot 事件适配与命令注册
- renderers 只负责“把数据变成图片 bytes”
- infra 负责 IO/存储/外部交互（可逐步迁移）
"""
