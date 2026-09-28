"""HLTV 帮助文案，使用项目公共帮助卡片。"""

from utils.rendering.models import HelpDocument, HelpEntry, HelpSection
from plugins.hltv_sub.config import plugin_config


def build_help_document() -> HelpDocument:
    return HelpDocument(
        title="HLTV 订阅插件帮助",
        introduction="先由管理员执行 hltv开启。赛事订阅全局共享，群开关只控制本群接收。",
        sections=(
            HelpSection(
                title="赛事相关", note="订阅需管理员",
                entries=(
                    HelpEntry(
                        command="event列表",
                        description="查看近期大型赛事列表（包含进行中/未开始），并标注你已订阅的赛事。",
                        aliases=("赛事列表", "events"),
                    ),
                    HelpEntry(
                        command="event订阅", arguments="[ID]",
                        description="订阅指定赛事，全局生效。存在已开启群时，启动及每 7 天会自动订阅近期赛事。",
                        aliases=("订阅赛事", "subscribe"), permission="群主/管理员",
                    ),
                    HelpEntry(
                        command="event取消订阅", arguments="[ID]",
                        description="全局取消订阅。若赛事仍在自动订阅范围内，后续自动订阅会重新加入。",
                        aliases=("取消订阅赛事", "unsubscribe"), permission="群主/管理员",
                    ),
                    HelpEntry(
                        command="我的订阅",
                        description="查看当前已订阅赛事及时间范围（订阅在所有已启用群全局同步）。",
                        aliases=("订阅列表", "mysub"),
                    ),
                ),
            ),
            HelpSection(
                title="比赛相关", note="查询类命令",
                entries=(
                    HelpEntry(
                        command="matches列表",
                        description="聚合查看所有已订阅赛事的对阵信息与开赛时间（单张图片分组展示）。",
                        aliases=("比赛列表", "matches"),
                    ),
                    HelpEntry(
                        command="results列表",
                        description="聚合查看所有已订阅赛事的最近结果（单张图片分组展示）。",
                        aliases=("结果列表", "results"),
                    ),
                    HelpEntry(
                        command="stats", arguments="[match_id]",
                        description="不带参数：按订阅顺序，显示首个有数据赛事的最新比赛；带 match_id：查看指定比赛数据。",
                    ),
                ),
            ),
            HelpSection(
                title="管理命令", note="仅群主/管理员",
                entries=(
                    HelpEntry(
                        command="hltv开启",
                        description="在本群启用查询与推送；无接收群时后台暂停抓取，重新开启后下一轮恢复。",
                        aliases=("hltv启用",), permission="群主/管理员",
                    ),
                    HelpEntry(
                        command="hltv关闭",
                        description="在本群禁用 HLTV 功能（需要管理员权限），停止响应命令与推送。",
                        aliases=("hltv禁用",), permission="群主/管理员",
                    ),
                ),
            ),
            HelpSection(
                title="调试命令", note="仅 bot 超级用户",
                entries=(
                    HelpEntry(
                        command="hltv_check",
                        description="（调试/超管）查看即将开始的比赛列表与提醒去重状态。",
                        permission="超级用户",
                    ),
                    HelpEntry(
                        command="hltv_trigger",
                        description="手动检查所有订阅赛事。报告发现的推送候选数，不代表发送成功数。",
                        permission="超级用户",
                    ),
                ),
            ),
            HelpSection(
                title="帮助", note="查看本页",
                entries=(
                    HelpEntry(
                        command="hltv帮助",
                        description="显示本帮助页面（图片形式），包含所有命令说明与权限标记。",
                        aliases=("hltvhelp",),
                    ),
                ),
            ),
        ),
        tips=("群关闭后普通查询不响应；管理员仍可开启，超级用户仍可使用调试命令。",),
        footer=plugin_config.hltv_watermark_text,
    )
