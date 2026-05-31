"""
groupmate_waifu/utils.py
工具函数：头像下载、@解析等
"""

import io
import hashlib
import asyncio
from typing import List

import httpx
from pil_utils import BuildImage
from nonebot.adapters.onebot.v11 import Message


# --- 头像下载相关 ---

async def download_url(url: str) -> bytes:
    """
    下载 URL 内容
    
    Args:
        url: 要下载的 URL
    
    Returns:
        下载的字节内容
    
    Raises:
        Exception: 下载失败时抛出
    """
    async with httpx.AsyncClient() as client:
        for i in range(3):
            try:
                resp = await client.get(url, timeout=20)
                resp.raise_for_status()
                return resp.content
            except Exception:
                await asyncio.sleep(3)
    raise Exception(f"{url} 下载失败！")


async def download_avatar(user_id: int) -> bytes:
    """
    下载用户头像
    
    Args:
        user_id: 用户 QQ 号
    
    Returns:
        头像图片的字节内容
    """
    url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
    data = await download_url(url)
    # 检查是否为默认头像
    if hashlib.md5(data).hexdigest() == "acef72340ac0e914090bd35799f5594e":
        url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
        data = await download_url(url)
    return data


async def download_user_img(user_id: int) -> bytes:
    """
    下载用户头像并转换为 PNG 格式
    
    Args:
        user_id: 用户 QQ 号
    
    Returns:
        PNG 格式的头像字节内容
    """
    data = await download_avatar(user_id)
    img = BuildImage.open(io.BytesIO(data))
    return img.save_png()


# --- 消息解析相关 ---

def get_message_at(message: Message) -> List[int]:
    """
    从消息中提取所有 @ 的用户 QQ 号

    注意：
        OneBot v11 中“@全体成员”的 at 段 `qq` 字段为字符串 "all"，
        不能直接 int()，这里跳过非数字的 qq 以避免异常。

    Args:
        message: 消息对象

    Returns:
        被 @ 的用户 QQ 号列表
    """
    qq_list: List[int] = []
    for msg in message:
        if msg.type != "at":
            continue

        qq = msg.data.get("qq")
        if qq is None:
            continue

        # 兼容 qq 为 int / str 的情况；"all" 等非数字直接忽略
        if isinstance(qq, int):
            qq_list.append(qq)
        elif isinstance(qq, str) and qq.isdigit():
            qq_list.append(int(qq))

    return qq_list
