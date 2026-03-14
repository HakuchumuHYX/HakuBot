# plugins/buaa_msm/handlers/bind.py
"""
绑定相关命令入口（私聊/群聊）。

迁移自 plugins/buaa_msm/bind.py（保持行为一致）。
"""

from __future__ import annotations

import json
from typing import Dict, Optional

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.rule import is_type

from ..config import plugin_config


class BindManager:
    """绑定数据管理器（单例模式）"""

    _instance: Optional["BindManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._data: Dict[str, str] = {}
        self._load()

    def _load(self):
        """加载绑定数据"""
        try:
            if plugin_config.bind_data_file.exists():
                with open(plugin_config.bind_data_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.success(f"已加载 {len(self._data)} 条绑定记录")
            else:
                self._data = {}
                logger.info("绑定数据文件不存在，将创建新文件")
        except Exception as e:
            logger.error(f"加载绑定数据失败: {e}")
            self._data = {}

    def _save(self) -> bool:
        """保存绑定数据"""
        try:
            with open(plugin_config.bind_data_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            logger.debug("绑定数据已保存")
            return True
        except Exception as e:
            logger.error(f"保存绑定数据失败: {e}")
            return False

    def get(self, user_id: str) -> Optional[str]:
        """获取用户绑定内容"""
        return self._data.get(user_id)

    def set(self, user_id: str, content: str) -> bool:
        """设置用户绑定内容"""
        old_content = self._data.get(user_id)
        self._data[user_id] = content

        if self._save():
            return True

        # 保存失败时回滚
        if old_content:
            self._data[user_id] = old_content
        else:
            self._data.pop(user_id, None)
        return False

    def delete(self, user_id: str) -> Optional[str]:
        """删除用户绑定，返回被删除的内容"""
        if user_id not in self._data:
            return None

        old_content = self._data[user_id]
        del self._data[user_id]

        if self._save():
            return old_content

        # 保存失败时恢复
        self._data[user_id] = old_content
        return None

    def get_all(self) -> Dict[str, str]:
        """获取所有绑定数据"""
        return self._data.copy()

    def reload(self):
        """重新加载数据"""
        self._load()


bind_manager = BindManager()

# ============== 私聊命令 ==============

bind_cmd = on_command("buaa绑定", rule=is_type(PrivateMessageEvent), priority=5, block=True)


@bind_cmd.handle()
async def handle_bind_command(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    bind_content = args.extract_plain_text().strip()

    if not bind_content:
        await bind_cmd.finish("请提供要绑定的内容，格式：buaa绑定+xxxx")
        return

    user_id = str(event.user_id)
    old_content = bind_manager.get(user_id)

    if bind_manager.set(user_id, bind_content):
        if old_content:
            await bind_cmd.finish(f"绑定成功！\n已更新绑定内容：\n原内容：{old_content}\n新内容：{bind_content}")
        else:
            await bind_cmd.finish(f"绑定成功！\nQQ：{user_id}\n绑定内容：{bind_content}")
    else:
        await bind_cmd.finish("绑定失败，数据保存出错，请稍后重试")


query_bind = on_command("buaa查询绑定", rule=is_type(PrivateMessageEvent), priority=5, block=True)


@query_bind.handle()
async def handle_query_command(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)
    content = bind_manager.get(user_id)

    if content:
        await query_bind.finish(f"您的绑定信息：\nQQ：{user_id}\n绑定内容：{content}")
    else:
        await query_bind.finish("您尚未绑定任何内容")


unbind_cmd = on_command("buaa解除绑定", rule=is_type(PrivateMessageEvent), priority=5, block=True)


@unbind_cmd.handle()
async def handle_unbind_command(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)
    old_content = bind_manager.delete(user_id)

    if old_content:
        await unbind_cmd.finish(f"已解除绑定\n原绑定内容：{old_content}")
    elif old_content is None and user_id not in bind_manager.get_all():
        await unbind_cmd.finish("您尚未绑定任何内容，无需解除")
    else:
        await unbind_cmd.finish("解除绑定失败，数据保存出错")


view_all_binds = on_command("buaa查看所有绑定", rule=is_type(PrivateMessageEvent), priority=10, block=True)


@view_all_binds.handle()
async def handle_view_all_command(bot: Bot, event: PrivateMessageEvent):
    superusers = get_driver().config.superusers
    if str(event.user_id) not in superusers:
        await view_all_binds.finish("权限不足，仅超级用户可使用此命令")
        return

    all_binds = bind_manager.get_all()
    if not all_binds:
        await view_all_binds.finish("当前没有任何绑定记录")
        return

    bind_list = [f"QQ：{qq} -> 内容：{content}" for qq, content in all_binds.items()]
    result = "当前所有绑定记录：\n" + "\n".join(bind_list)

    if len(result) > 500:
        parts = [result[i : i + 500] for i in range(0, len(result), 500)]
        for part in parts:
            await bot.send_private_msg(user_id=event.user_id, message=part)
        await view_all_binds.finish("以上是所有绑定记录")
    else:
        await view_all_binds.finish(result)


# ============== 群聊提示命令 ==============

group_bind_cmd = on_command("buaa绑定", rule=is_type(GroupMessageEvent), priority=5, block=True)


@group_bind_cmd.handle()
async def handle_group_bind(bot: Bot, event: GroupMessageEvent):
    await group_bind_cmd.finish("该指令仅在私聊中可用")


group_query_cmd = on_command("buaa查询绑定", rule=is_type(GroupMessageEvent), priority=5, block=True)


@group_query_cmd.handle()
async def handle_group_query(bot: Bot, event: GroupMessageEvent):
    await group_query_cmd.finish("该指令仅在私聊中可用")


group_unbind_cmd = on_command("buaa解除绑定", rule=is_type(GroupMessageEvent), priority=5, block=True)


@group_unbind_cmd.handle()
async def handle_group_unbind(bot: Bot, event: GroupMessageEvent):
    await group_unbind_cmd.finish("该指令仅在私聊中可用")


group_view_all_cmd = on_command("buaa查看所有绑定", rule=is_type(GroupMessageEvent), priority=10, block=True)


@group_view_all_cmd.handle()
async def handle_group_view_all(bot: Bot, event: GroupMessageEvent):
    await group_view_all_cmd.finish("该指令仅在私聊中可用")


logger.success("绑定 handlers 已加载成功！")
