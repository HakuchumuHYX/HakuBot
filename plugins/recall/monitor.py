import time
from collections import deque
from typing import Dict, Any, NamedTuple
from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, GroupRecallNoticeEvent, Message
from nonebot.log import logger
from ..plugin_manager.enable import is_feature_enabled
from .config import config

# 缓存消息结构
class CachedMessage(NamedTuple):
    message_id: int
    content: Message
    sender_id: int
    sender_name: str
    group_id: int
    timestamp: float

# 消息缓存：使用 deque 限制最大数量，同时配合字典快速查找
# 这里简单使用字典 + 定期/被动清理，或者直接限制字典大小
# 为了简单，使用一个定长 deque 来维护 ID 顺序，配合字典存内容
MAX_CACHE_SIZE = 5000
msg_cache: Dict[int, CachedMessage] = {}
msg_id_queue = deque(maxlen=MAX_CACHE_SIZE)

# 监听群消息，存入缓存
# priority=1, block=False 确保不影响其他插件
record_msg = on_message(priority=1, block=False)

@record_msg.handle()
async def _(event: GroupMessageEvent):
    # 记录消息
    try:
        msg_id = event.message_id
        content = event.message
        sender_id = event.user_id
        sender_name = event.sender.card or event.sender.nickname or str(sender_id)
        group_id = event.group_id
        
        # 存入缓存
        if msg_id in msg_cache:
            return
            
        # 如果队列满了，移除最旧的（deque会自动移除，但我们需要同步删除字典里的）
        if len(msg_id_queue) >= MAX_CACHE_SIZE:
            oldest_id = msg_id_queue[0] # peek
            # 注意：这里不能直接 pop，因为 deque(maxlen) 会在 append 时自动 pop
            # 但我们需要知道 pop 了谁来删字典。
            # 所以手动判断：
            # 实际上 deque maxlen 自动处理有点麻烦，我们手动处理比较稳
            pass

        msg_id_queue.append(msg_id)
        # 如果队列长度超过限制（说明刚才自动挤出去了一个，或者我们需要手动挤）
        # 使用 deque 的 maxlen 特性，它会自动丢弃左边的。
        # 但我们无法知道丢弃了谁。所以不使用 maxlen，而是手动 popleft
        
        while len(msg_id_queue) > MAX_CACHE_SIZE:
            removed_id = msg_id_queue.popleft()
            if removed_id in msg_cache:
                del msg_cache[removed_id]

        msg_cache[msg_id] = CachedMessage(
            message_id=msg_id,
            content=content,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            timestamp=time.time()
        )
        
    except Exception as e:
        logger.error(f"撤回监控-缓存消息失败: {e}")

# 监听撤回事件
recall_notice = on_notice(priority=1, block=False)

@recall_notice.handle()
async def _(bot: Bot, event: GroupRecallNoticeEvent):
    msg_id = event.message_id
    group_id = event.group_id
    operator_id = event.operator_id # 撤回者
    
    # 1. 检查开关
    if not is_feature_enabled("recall", "monitor", str(group_id), str(operator_id)):
        return

    # 2. 检查缓存中是否有该消息
    if msg_id not in msg_cache:
        # 可能是重启前的消息，或者缓存已过期
        return
        
    cached_msg = msg_cache[msg_id]
    
    # 3. 获取目标群
    target_group = config.target_group
    if not target_group or not target_group.isdigit():
        # 未配置或配置无效，不发送
        return
        
    target_group_id = int(target_group)
    
    try:
        # 构建转发消息
        # 格式：
        # 检测到撤回消息：
        # 来源群：[群号]
        # 发送者：[名字] ([QQ])
        # 撤回者：[名字] ([QQ]) (如果是自己撤回，可以简化)
        # 内容：...
        
        # 获取撤回者信息
        operator_name = str(operator_id)
        try:
            member_info = await bot.get_group_member_info(group_id=group_id, user_id=operator_id)
            operator_name = member_info.get("card") or member_info.get("nickname") or str(operator_id)
        except:
            pass

        text_header = (
            f"检测到撤回消息\n"
            f"来源群：{group_id}\n"
            f"发送者：{cached_msg.sender_name} ({cached_msg.sender_id})\n"
        )
        
        if operator_id != cached_msg.sender_id:
            text_header += f"撤回者：{operator_name} ({operator_id})\n"
            
        text_header += "消息内容：\n"
        
        # 发送组合消息
        # 先发文本头，再发内容，或者合并
        # 为了展示图片，直接把内容拼在后面
        # Message(str) + Message
        full_msg = Message(text_header) + cached_msg.content
        
        await bot.send_group_msg(group_id=target_group_id, message=full_msg)
        logger.info(f"已转发群 {group_id} 的撤回消息到 {target_group_id}")
        
    except Exception as e:
        logger.error(f"转发撤回消息失败: {e}")
