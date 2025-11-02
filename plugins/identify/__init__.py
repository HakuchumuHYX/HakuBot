import asyncio
from nonebot import get_driver, on_command, on_message, logger
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    Bot,
    MessageEvent
)
from nonebot.rule import to_me
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg

# 导入管理模块
from ..plugin_manager import is_plugin_enabled
from ..utils.common import create_exact_command_rule
from .data_manager import daily_record_manager
from nonebot.exception import FinishedException

# 注册命令处理器 - 移除原有的启用/禁用命令，只保留鉴定命令
identify = on_command("鉴定", priority=5, block=True, rule=create_exact_command_rule("鉴定"))


async def create_identify_message(bot: Bot, group_id: int, user_id: int, image_path: str) -> list:
    """
    创建鉴定合并转发消息

    Args:
        bot: 机器人实例
        group_id: 群组ID
        user_id: 用户ID
        image_path: 图片路径

    Returns:
        合并转发消息节点列表
    """
    try:
        # 获取用户信息
        user_info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        user_name = user_info.get("card") or user_info.get("nickname", "用户")

        # 获取机器人信息
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "鉴定机器人")

        # 创建转发消息节点
        forward_nodes = [
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": str(bot.self_id),
                    "content": f"呀吼！@{user_name}，经鉴定你是"
                }
            },
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": str(bot.self_id),
                    "content": Message(MessageSegment.image(f"file:///{image_path}"))
                }
            }
        ]

        return forward_nodes

    except Exception as e:
        logger.error(f"创建鉴定消息失败: {e}")
        # 失败时返回简单的文本消息格式
        return [
            {
                "type": "node",
                "data": {
                    "name": "鉴定结果",
                    "uin": str(bot.self_id),
                    "content": f"鉴定完成！今日鉴定结果已生成"
                }
            }
        ]


@identify.handle()
async def handle_identify(bot: Bot, event: GroupMessageEvent):
    """处理鉴定命令"""
    try:
        group_id = event.group_id
        user_id = event.user_id

        # 检查群组是否启用鉴定功能 - 改用管理插件
        if not is_plugin_enabled("identify", str(group_id)):
            await identify.finish()
            return

        # 检查用户今天的鉴定结果
        today_record = daily_record_manager.get_user_record(group_id, user_id)

        if today_record:
            # 如果今天已经鉴定过，直接返回之前的结果
            image_path = today_record
        else:
            # 获取随机图片
            image_path = daily_record_manager.get_random_image()

            if not image_path:
                await identify.finish("鉴定系统暂时不可用，请稍后再试喵~")
                return

            # 保存用户今天的鉴定结果
            daily_record_manager.set_user_record(group_id, user_id, image_path)

        # 创建合并转发消息
        forward_nodes = await create_identify_message(bot, group_id, user_id, image_path)

        # 发送合并转发消息
        await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)

        logger.info(f"用户 {user_id} 在群 {group_id} 进行了鉴定")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"处理鉴定命令时出错: {e}")
        await identify.finish("鉴定失败，请稍后再试喵~")


async def daily_cleanup():
    """每日清理任务"""
    while True:
        try:
            # 计算到下一个0点的时间
            now = asyncio.get_event_loop().time()
            next_cleanup = ((now // 86400) + 1) * 86400  # 下一个UTC 0点
            wait_time = next_cleanup - now

            # 等待到0点
            await asyncio.sleep(wait_time)

            # 执行清理
            daily_record_manager.cleanup_old_records()
            logger.info("已执行每日鉴定记录清理")

        except Exception as e:
            logger.error(f"每日清理任务出错: {e}")
            # 出错时等待1小时后重试
            await asyncio.sleep(3600)


# 插件启动时初始化
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    # 启动每日清理任务
    asyncio.create_task(daily_cleanup())

    # 清理旧记录
    daily_record_manager.cleanup_old_records()

    logger.info("鉴定插件初始化完成")


# 机器人关闭时的处理
@get_driver().on_shutdown
async def shutdown_plugin():
    """插件关闭"""
    logger.info("鉴定插件已关闭")