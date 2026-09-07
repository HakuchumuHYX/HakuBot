import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Union, Optional, Sequence, List
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment
from nonebot.exception import NetworkError, ActionFailed
from nonebot.log import logger
from utils.logging import get_exc_desc


@dataclass(frozen=True)
class ForwardItem:
    content: Union[str, Message, MessageSegment]
    name: Optional[str] = None
    uin: Optional[Union[str, int]] = None


class ForwardStatus(str, Enum):
    SENT = "sent"
    FALLBACK_SENT = "fallback_sent"
    TIMEOUT_UNKNOWN = "timeout_unknown"


class ForwardSendError(RuntimeError):
    def __init__(self, message: str, *, sent: int = 0, failed: int = 0) -> None:
        super().__init__(message)
        self.sent = sent
        self.failed = failed


def _as_message(content: Union[str, Message, MessageSegment]) -> Message:
    if isinstance(content, Message):
        return content
    if isinstance(content, MessageSegment):
        return Message(content)
    return Message(content)


async def send_forward_msg(
    bot: Bot,
    event: Optional[Event] = None,
    *,
    items: Sequence[Union[ForwardItem, str, Message, MessageSegment]],
    group_id: Optional[int] = None,
    user_id: Optional[int] = None,
    timeout: float = 60.0,
    fallback_on_action_failed: bool = True,
    fallback_interval: float = 1.0,
) -> ForwardStatus:
    """Send structured forward nodes with timeout-safe fallback semantics."""
    forward_items = list(items)
    if not forward_items:
        raise ValueError("合并转发消息不能为空")

    event_group_id = getattr(event, "group_id", None) if event is not None else None
    event_user_id = None
    if event is not None and event_group_id is None:
        event_user_id = int(event.get_user_id())
    if group_id is not None or user_id is not None:
        event_group_id = event_user_id = None
    resolved_group_id = (
        int(group_id if group_id is not None else event_group_id)
        if (group_id is not None or event_group_id is not None)
        else None
    )
    resolved_user_id = (
        int(user_id if user_id is not None else event_user_id)
        if (user_id is not None or event_user_id is not None)
        else None
    )
    if (resolved_group_id is None) == (resolved_user_id is None):
        raise ValueError("必须且只能指定一个群聊或私聊目标")

    try:
        login_info = await bot.get_login_info()
        default_uin = str(login_info.get("user_id", bot.self_id))
        default_name = str(login_info.get("nickname") or "Bot")
    except Exception:
        default_uin = str(bot.self_id)
        default_name = "Bot"

    normalized_items = [
        item if isinstance(item, ForwardItem) else ForwardItem(content=item)
        for item in forward_items
    ]
    nodes = [
        {
            "type": "node",
            "data": {
                "name": item.name or default_name,
                "uin": str(item.uin if item.uin is not None else default_uin),
                "content": _as_message(item.content),
            },
        }
        for item in normalized_items
    ]

    api_name = (
        "send_group_forward_msg"
        if resolved_group_id is not None
        else "send_private_forward_msg"
    )
    target_data = (
        {"group_id": resolved_group_id}
        if resolved_group_id is not None
        else {"user_id": resolved_user_id}
    )
    try:
        await bot.call_api(api_name, messages=nodes, _timeout=timeout, **target_data)
    except NetworkError as e:
        logger.warning(
            f"合并转发网络结果未知 api={api_name} nodes={len(nodes)}: {get_exc_desc(e)}；"
            "跳过补发以避免重复消息"
        )
        return ForwardStatus.TIMEOUT_UNKNOWN
    except asyncio.TimeoutError as e:
        logger.warning(
            f"合并转发等待超时 api={api_name} nodes={len(nodes)}: {get_exc_desc(e)}；"
            "跳过补发以避免重复消息"
        )
        return ForwardStatus.TIMEOUT_UNKNOWN
    except ActionFailed as e:
        if not fallback_on_action_failed:
            raise ForwardSendError(f"合并转发被 OneBot 拒绝: {get_exc_desc(e)}") from e
        logger.warning(
            f"合并转发被 OneBot 拒绝，开始逐条补发 nodes={len(nodes)}: {get_exc_desc(e)}"
        )
        sent = 0
        failures: List[str] = []
        for index, item in enumerate(normalized_items):
            try:
                message = _as_message(item.content)
                if event is not None and group_id is None and user_id is None:
                    await bot.send(event, message)
                elif resolved_group_id is not None:
                    await bot.send_group_msg(
                        group_id=resolved_group_id, message=message
                    )
                else:
                    await bot.send_private_msg(
                        user_id=resolved_user_id, message=message
                    )
                sent += 1
            except Exception as fallback_error:
                failures.append(f"#{index + 1} {get_exc_desc(fallback_error)}")
            if fallback_interval > 0 and index < len(normalized_items) - 1:
                await asyncio.sleep(fallback_interval)
        if failures:
            raise ForwardSendError(
                f"合并转发降级部分失败: {'; '.join(failures)}",
                sent=sent,
                failed=len(failures),
            ) from e
        return ForwardStatus.FALLBACK_SENT
    return ForwardStatus.SENT
