# plugins/buaa_msm/exceptions.py
"""
buaa_msm 结构化异常定义。

用于在 service/handler 层区分错误类型，避免全部退化为裸 Exception。
"""


class BUAAMSMError(Exception):
    """插件基础异常。"""


class DataLoadError(BUAAMSMError):
    """用户数据加载/解析错误。"""


class AssetDownloadError(BUAAMSMError):
    """外部资源下载失败。"""


class RenderError(BUAAMSMError):
    """图像渲染失败。"""


class SendError(BUAAMSMError):
    """消息发送失败。"""
