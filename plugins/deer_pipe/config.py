"""deer_pipe 插件配置模块"""

from pydantic import BaseModel


class DeerPipeConfig(BaseModel):
    """deer_pipe 插件配置"""

    # CD 时间（秒），0 表示无冷却
    cd_time: int = 0

    # 是否启用补签功能
    enable_past_deer: bool = True

    # 是否启用帮他人签到功能
    enable_help_deer: bool = True

    # 日历图片质量 (1-100)
    image_quality: int = 95

    # 签到成功时的提示语
    success_message: str = "成功🦌了"
    help_success_message: str = "成功帮{target}🦌了"
    past_success_message: str = "成功补🦌"

    # 错误提示语
    disabled_message: str = "🦌签到功能当前已被禁用"
    cd_message: str = "🦌功能还在冷却中，请等待 {remaining} 秒"
    invalid_date_message: str = "不是合法的补🦌日期捏"
    already_signed_message: str = "不能补🦌已经🦌过的日子捏"


# 默认配置实例
config = DeerPipeConfig()
