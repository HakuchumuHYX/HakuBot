"""Help content only; appearance and fallback are shared."""

from utils.rendering.models import HelpDocument, HelpSection, HelpEntry

HELP = HelpDocument(
    title="图像处理工具箱",
    sections=(
        HelpSection(
            "GIF / 动画",
            (
                HelpEntry("img倒放", "将 GIF 倒序播放"),
                HelpEntry("imgx", "调整 GIF 播放速度（0.1–5.0）", "[倍数]"),
                HelpEntry("img旋转", "让图片自转生成 GIF，默认顺时针", "[倍速]"),
                HelpEntry(
                    "img顺时针 / img逆时针", "指定旋转方向，支持静态图和 GIF", "[倍速]"
                ),
            ),
        ),
        HelpSection(
            "图片编辑",
            (
                HelpEntry("imgcut", "智能去除背景"),
                HelpEntry("img镜像 / img上镜像", "水平或垂直翻转"),
                HelpEntry("img对称 / img左对称", "左半镜像"),
                HelpEntry(
                    "img右对称 / img上对称 / img下对称 / img中心对称",
                    "按指定方向生成对称图片",
                ),
            ),
        ),
        HelpSection("视频", (HelpEntry("imggif", "视频转 GIF，限 60 秒"),)),
    ),
    tips=("请回复包含图片或视频的消息使用。",),
)
