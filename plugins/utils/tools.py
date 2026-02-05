import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any, TypeVar, Union, List
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment
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

async def send_forward_msg(
    bot: Bot, 
    event: Event, 
    messages: List[Union[str, Message, MessageSegment]]
):
    """
    发送合并转发消息
    """
    # 获取 Bot 信息作为发送者
    try:
        login_info = await bot.get_login_info()
        user_id = str(login_info.get("user_id", event.self_id))
        nickname = login_info.get("nickname", "Bot")
    except Exception:
        user_id = str(event.self_id)
        nickname = "Bot"
    
    nodes = []
    for msg in messages:
        nodes.append({
            "type": "node",
            "data": {
                "name": nickname,
                "uin": user_id,
                "content": msg
            }
        })
    
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        else:
            # 尝试发送私聊合并转发，如果不直接支持可能需要 fallback
            # 大多数 OneBot 实现支持 send_private_forward_msg
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
    except NetworkError as e:
        logger.error(f"发送合并转发网络错误: {e}")
        if "timeout" in str(e).lower():
            logger.warning("合并转发请求超时，服务端可能仍在处理中，跳过 fallback 以避免重复发送。")
        else:
            # 非超时网络错误，尝试逐条发送
            for msg in messages:
                if isinstance(msg, str):
                    await bot.send(event, Message(msg))
                else:
                    await bot.send(event, msg)
    except (ActionFailed, Exception) as e:
        logger.error(f"发送合并转发失败 ({type(e).__name__}): {e}")
        # 其他错误（如API调用失败），尝试逐条发送
        for msg in messages:
            if isinstance(msg, str):
                await bot.send(event, Message(msg))
            else:
                await bot.send(event, msg)
