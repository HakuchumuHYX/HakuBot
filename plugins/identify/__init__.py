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
from typing import Optional

# 导入管理模块
from ..plugin_manager.enable import is_plugin_enabled
from ..utils.image_utils import path_to_base64_image
from ..utils.common import create_exact_command_rule
from .data_manager import daily_record_manager
from nonebot.exception import FinishedException

# 注册命令处理器 - 鉴定自己
identify = on_command("鉴定", priority=5, block=True, rule=create_exact_command_rule("鉴定"))

# 注册命令处理器 - 鉴定别人
identify_other = on_command("鉴定你", priority=4, block=True)


async def create_identify_message(
    bot: Bot, 
    group_id: int, 
    target_user_id: int, 
    image_path: str,
    initiator_user_id: Optional[int] = None
) -> list:
    """
    创建鉴定合并转发消息

    Args:
        bot: 机器人实例
        group_id: 群组ID
        target_user_id: 被鉴定的用户ID
        image_path: 图片路径
        initiator_user_id: 发起鉴定的用户ID（如果是鉴定别人的情况）

    Returns:
        合并转发消息节点列表
    """
    try:
        # 获取被鉴定用户信息
        target_info = await bot.get_group_member_info(group_id=group_id, user_id=target_user_id)
        target_name = target_info.get("card") or target_info.get("nickname", "用户")

        # 获取机器人信息
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "鉴定机器人")

        # 根据是否是鉴定别人来生成不同的文案
        if initiator_user_id is not None and initiator_user_id != target_user_id:
            # 鉴定别人的情况
            initiator_info = await bot.get_group_member_info(group_id=group_id, user_id=initiator_user_id)
            initiator_name = initiator_info.get("card") or initiator_info.get("nickname", "用户")
            text_content = f"呀吼！@{initiator_name} 对 @{target_name} 的鉴定结果：\n@{target_name} 是"
        else:
            # 鉴定自己的情况
            text_content = f"呀吼！@{target_name}，经鉴定你是"

        # 创建转发消息节点
        forward_nodes = [
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": str(bot.self_id),
                    "content": text_content
                }
            },
            {
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": str(bot.self_id),
                    "content": Message(path_to_base64_image(image_path))
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


def get_at_target(event: GroupMessageEvent) -> Optional[int]:
    """
    从消息中提取被@的用户ID
    
    Args:
        event: 群消息事件
        
    Returns:
        被@的用户ID，如果没有@则返回None
    """
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq and qq != "all":  # 排除@全体成员
                return int(qq)
    return None


@identify.handle()
async def handle_identify(bot: Bot, event: GroupMessageEvent):
    """处理鉴定命令（鉴定自己）"""
    try:
        group_id = event.group_id
        user_id = event.user_id
        user_id_str = str(event.user_id)
        # 检查群组是否启用鉴定功能 - 改用管理插件
        if not is_plugin_enabled("identify", str(group_id), user_id_str):
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


@identify_other.handle()
async def handle_identify_other(bot: Bot, event: GroupMessageEvent):
    """处理鉴定别人命令（鉴定你+@xxx）"""
    try:
        group_id = event.group_id
        initiator_id = event.user_id  # 发起鉴定的人
        user_id_str = str(event.user_id)
        
        # 检查群组是否启用鉴定功能
        if not is_plugin_enabled("identify", str(group_id), user_id_str):
            await identify_other.finish()
            return

        # 获取被@的用户
        target_user_id = get_at_target(event)
        
        if target_user_id is None:
            await identify_other.finish("请@一个要鉴定的人喵~\n用法：鉴定你 @某人")
            return
        
        # 不能鉴定机器人自己
        if target_user_id == int(bot.self_id):
            await identify_other.finish("我不能鉴定我自己喵~")
            return

        # 检查被鉴定用户今天的鉴定结果
        today_record = daily_record_manager.get_user_record(group_id, target_user_id)

        if today_record:
            # 如果今天已经被鉴定过，直接返回之前的结果
            image_path = today_record
        else:
            # 获取随机图片
            image_path = daily_record_manager.get_random_image()

            if not image_path:
                await identify_other.finish("鉴定系统暂时不可用，请稍后再试喵~")
                return

            # 保存被鉴定用户今天的鉴定结果
            daily_record_manager.set_user_record(group_id, target_user_id, image_path)

        # 创建合并转发消息（传入发起者ID以生成不同文案）
        forward_nodes = await create_identify_message(
            bot, group_id, target_user_id, image_path, initiator_user_id=initiator_id
        )

        # 发送合并转发消息
        await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)

        logger.info(f"用户 {initiator_id} 在群 {group_id} 鉴定了用户 {target_user_id}")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"处理鉴定别人命令时出错: {e}")
        await identify_other.finish("鉴定失败，请稍后再试喵~")


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
