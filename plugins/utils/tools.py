import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Any, TypeVar, Union, List, Optional, Sequence
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment
from nonebot.exception import NetworkError, ActionFailed

T = TypeVar("T")

def get_logger(name: str):
    """
    获取带名称的 logger，实际上是对 nonebot logger 的简单封装
    """
    return logger.bind(name=name)

_pool = ThreadPoolExecutor(max_workers=16)

async def run_in_pool(func: Callable[..., T], *args, **kwargs) -> T:
    """
    在线程池中运行同步函数
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, lambda: func(*args, **kwargs))

def truncate(text: str, length: int = 50) -> str:
    """
    截断过长的字符串
    """
    if len(text) > length:
        return text[:length] + "..."
    return text

def get_exc_desc(e: Exception) -> str:
    """
    获取异常的简短描述
    """
    return f"{type(e).__name__}: {str(e)}"

import tempfile
import os
from pathlib import Path
from datetime import timedelta

class TempFilePath:
    """
    临时文件路径管理器，支持 with 语句自动清理
    """
    def __init__(self, ext: str = None, remove_after: Union[bool, int, float, timedelta] = True):
        self.ext = ext or 'tmp'
        self.remove_after = remove_after
        self.path: Path = None

    def __enter__(self) -> Path:
        fd, path = tempfile.mkstemp(suffix=f'.{self.ext}')
        os.close(fd)
        self.path = Path(path)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.path and self.path.exists():
            if self.remove_after is True:
                try:
                    os.remove(self.path)
                except Exception as e:
                    logger.warning(f"删除临时文件 {self.path} 失败: {e}")
            elif self.remove_after:
                # 延迟删除
                delay = 0
                if isinstance(self.remove_after, (int, float)):
                    delay = self.remove_after
                elif isinstance(self.remove_after, timedelta):
                    delay = self.remove_after.total_seconds()
                
                if delay > 0:
                    async def delayed_remove(p: Path, d: float):
                        await asyncio.sleep(d)
                        if p.exists():
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                    
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(delayed_remove(self.path, delay))
                    except RuntimeError:
                        # 如果没有运行的 loop，直接删除
                        try:
                            os.remove(self.path)
                        except:
                            pass
        return False

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
    messages: Optional[Sequence[Union[ForwardItem, str, Message, MessageSegment]]] = None,
    *,
    items: Optional[Sequence[Union[ForwardItem, str, Message, MessageSegment]]] = None,
    group_id: Optional[int] = None,
    user_id: Optional[int] = None,
    timeout: float = 60.0,
    fallback_on_action_failed: bool = True,
    fallback_interval: float = 1.0,
) -> ForwardStatus:
    """Send structured forward nodes with timeout-safe fallback semantics."""
    if messages is not None and items is not None:
        raise ValueError("messages 和 items 不能同时传入")
    forward_items = list(items if items is not None else messages or [])
    if not forward_items:
        raise ValueError("合并转发消息不能为空")

    event_group_id = getattr(event, "group_id", None) if event is not None else None
    event_user_id = None
    if event is not None and event_group_id is None:
        event_user_id = int(event.get_user_id())
    resolved_group_id = int(group_id if group_id is not None else event_group_id) if (group_id is not None or event_group_id is not None) else None
    resolved_user_id = int(user_id if user_id is not None else event_user_id) if (user_id is not None or event_user_id is not None) else None
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

    api_name = "send_group_forward_msg" if resolved_group_id is not None else "send_private_forward_msg"
    target_data = {"group_id": resolved_group_id} if resolved_group_id is not None else {"user_id": resolved_user_id}
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
                if event is not None:
                    await bot.send(event, message)
                elif resolved_group_id is not None:
                    await bot.send_group_msg(group_id=resolved_group_id, message=message)
                else:
                    await bot.send_private_msg(user_id=resolved_user_id, message=message)
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
