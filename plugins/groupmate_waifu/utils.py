import io
import httpx
import hashlib
import asyncio

from pil_utils import BuildImage,Text2Image
from nonebot.adapters.onebot.v11 import Message
from nonebot.log import logger

async def download_avatar(user_id: int) -> bytes:
    url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
    data = await download_url(url)
    if hashlib.md5(data).hexdigest() == "acef72340ac0e914090bd35799f5594e":
        url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
        data = await download_url(url)
    return data

async def download_url(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        for i in range(3):
            try:
                resp = await client.get(url, timeout=20)
                resp.raise_for_status()
                return resp.content
            except Exception:
                await asyncio.sleep(3)
    raise Exception(f"{url} 下载失败！")

async def download_user_img(user_id: int):
    data = await download_avatar(user_id)
    img = BuildImage.open(io.BytesIO(data))
    return img.save_png()

async def user_img(user_id: int) -> bytes:
    '''
    获取用户头像url
    '''
    url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
    data = await download_url(url)
    if hashlib.md5(data).hexdigest() == "acef72340ac0e914090bd35799f5594e":
        url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
    return url


def text_to_png(msg):
    '''
    文字转png
    '''
    output = io.BytesIO()
    try:
        # 先创建文本图像对象
        text_img = Text2Image.from_text(msg, 50)
        # 设置一个合理的最大宽度（例如800像素）
        text_img.wrap(800)
        # 生成透明背景的文本图片
        img = text_img.to_image()

        # 创建一个白色背景的图片，大小比文本图片稍大
        bg_width = img.width + 40
        bg_height = img.height + 40
        bg = BuildImage.new("RGB", (bg_width, bg_height), "white")

        # 将文本图片粘贴到白色背景上
        bg.paste(img, (20, 20))

        # 保存为PNG
        bg.save(output, format="png")

    except Exception as e:
        logger.error(f"text_to_png error: {e}")
        # 如果失败，尝试更简单的方法
        try:
            # 直接创建一个白色背景的图片，并在上面绘制文本
            from PIL import Image, ImageDraw, ImageFont
            # 估算文本大小
            font = ImageFont.truetype("msyh.ttc", 50)  # 使用系统字体
            # 计算文本尺寸
            dummy_img = Image.new("RGB", (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            bbox = dummy_draw.textbbox((0, 0), msg, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # 创建白色背景图片
            img = Image.new("RGB", (text_width + 40, text_height + 40), "white")
            draw = ImageDraw.Draw(img)

            # 绘制文本
            draw.text((20, 20), msg, fill="black", font=font)

            # 保存
            img.save(output, format="png")
        except Exception as e2:
            logger.error(f"text_to_png fallback error: {e2}")
            # 最后尝试返回纯文本
            raise Exception(f"无法生成图片: {e2}")

    return output


def bbcode_to_png(msg, spacing: int = 10):
    '''
    bbcode文字转png
    '''
    output = io.BytesIO()
    try:
        # 先创建文本图像对象
        text_img = Text2Image.from_bbcode_text(msg, 50)
        # 设置一个合理的最大宽度（例如800像素）
        text_img.wrap(800)
        # 生成透明背景的文本图片
        img = text_img.to_image()

        # 创建一个白色背景的图片，大小比文本图片稍大
        bg_width = img.width + 40
        bg_height = img.height + 40
        bg = BuildImage.new("RGB", (bg_width, bg_height), "white")

        # 将文本图片粘贴到白色背景上
        bg.paste(img, (20, 20))

        # 保存为PNG
        bg.save(output, format="png")

    except Exception as e:
        logger.error(f"bbcode_to_png error: {e}")
        # 如果失败，使用普通文本方法
        return text_to_png(msg.replace("[align=left]", "").replace("[/align]", "").replace("[align=right]", ""))

    return output

def get_message_at(message:Message) -> list:
    '''
    获取at列表
    '''
    qq_list = []
    for msg in message:
        if msg.type == "at":
            qq_list.append(int(msg.data["qq"]))
    return qq_list
