from utils.rendering.models import HelpDocument, HelpSection, HelpEntry


def build_help_document(game_service):
    entries = tuple(
        HelpEntry(
            "猜歌",
            value["name"],
            ("" if mode == "normal" else mode) + f"（{value['score']}分）",
        )
        for mode, value in game_service.game_modes.items()
    )
    return HelpDocument(
        "PJSK 猜歌帮助",
        sections=(
            HelpSection("基础指令", entries),
            HelpSection(
                "高级指令",
                (
                    HelpEntry("随机猜歌", "随机组合效果，分数按组合计算"),
                    HelpEntry("猜歌手", "竞猜演唱者（1分）"),
                    HelpEntry(
                        "听",
                        "播放指定歌曲，可选 vs 或 sekai 版，默认 sekai 版",
                        "[歌名/ID] <ver>",
                    ),
                    HelpEntry(
                        "听<模式>", "支持钢琴、伴奏、人声、贝斯、鼓组", "[歌名/ID]"
                    ),
                    HelpEntry(
                        "听anvo",
                        "播放指定或随机的 Another Vocal",
                        "[歌名/ID] [角色名缩写]",
                    ),
                ),
            ),
            HelpSection(
                "其他功能",
                (
                    HelpEntry("猜歌帮助", "显示帮助"),
                    HelpEntry("猜歌资源", "显示资源版本"),
                ),
            ),
        ),
    )
