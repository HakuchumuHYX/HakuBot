"""
groupmate_waifu/utils.py
工具函数：头像下载、文字转图片、@解析等
"""

import io
import hashlib
import asyncio
from typing import List

import httpx
from pil_utils import BuildImage, Text2Image
from nonebot.adapters.onebot.v11 import Message
from nonebot.log import logger


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


async def user_img(user_id: int) -> str:
    """
    获取用户头像 URL
    
    Args:
        user_id: 用户 QQ 号
    
    Returns:
        头像 URL 字符串
    """
    url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
    data = await download_url(url)
    # 检查是否为默认头像，如果是则使用小尺寸
    if hashlib.md5(data).hexdigest() == "acef72340ac0e914090bd35799f5594e":
        url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
    return url


# --- 文字转图片相关 ---

def text_to_png(msg: str) -> io.BytesIO:
    """
    将文字转换为 PNG 图片
    
    Args:
        msg: 要转换的文字内容
    
    Returns:
        包含 PNG 图片数据的 BytesIO 对象
    """
    output = io.BytesIO()
    try:
        # 创建文本图像对象
        text_img = Text2Image.from_text(msg, 50)
        # 设置最大宽度
        text_img.wrap(800)
        # 生成透明背景的文本图片 (RGBA)
        img = text_img.to_image()

        # 创建白色背景图片 (RGB)
        bg_width = img.width + 40
        bg_height = img.height + 40
        bg = BuildImage.new("RGB", (bg_width, bg_height), "white")

        # 将文本图片粘贴到白色背景上，启用 alpha 蒙版
        bg.paste(img, (20, 20), alpha=True)

        # 保存为 PNG
        bg.image.save(output, format="png")

    except Exception as e:
        logger.error(f"text_to_png error: {e}")
        # fallback：使用 PIL 直接绘制
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            font_path = "msyh.ttc"  # 微软雅黑
            try:
                font = ImageFont.truetype(font_path, 50)
            except IOError:
                logger.warning(f"找不到字体 {font_path}，使用默认字体。")
                font = ImageFont.load_default()

            # 计算文本尺寸
            dummy_img = Image.new("RGB", (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), msg, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # 创建白色背景图片
            img = Image.new("RGB", (text_width + 40, text_height + 40), "white")
            draw = ImageDraw.Draw(img)
            draw.text((20, 20), msg, fill="black", font=font)
            img.save(output, format="png")
            
        except Exception as e2:
            logger.error(f"text_to_png fallback error: {e2}")
            raise Exception(f"无法生成图片: {e2}")

    return output


def bbcode_to_png(msg: str, spacing: int = 10) -> io.BytesIO:
    """
    将 BBCode 格式的文字转换为 PNG 图片
    
    Args:
        msg: 要转换的 BBCode 文字内容
        spacing: 行间距（暂未使用）
    
    Returns:
        包含 PNG 图片数据的 BytesIO 对象
    """
    output = io.BytesIO()
    try:
        # 创建文本图像对象
        text_img = Text2Image.from_bbcode_text(msg, 50)
        # 设置最大宽度
        text_img.wrap(800)
        # 生成透明背景的文本图片 (RGBA)
        img = text_img.to_image()

        # 创建白色背景图片 (RGB)
        bg_width = img.width + 40
        bg_height = img.height + 40
        bg = BuildImage.new("RGB", (bg_width, bg_height), "white")

        # 将文本图片粘贴到白色背景上，启用 alpha 蒙版
        bg.paste(img, (20, 20), alpha=True)

        # 保存为 PNG
        bg.image.save(output, format="png")

    except Exception as e:
        logger.error(f"bbcode_to_png error: {e}")
        # fallback：移除 BBCode 标签后使用普通文本转换
        clean_msg = (msg
            .replace("[align=left]", "")
            .replace("[/align]", "")
            .replace("[align=right]", ""))
        return text_to_png(clean_msg)

    return output


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
