"""deer_pipe 插件图片生成模块"""

from calendar import monthcalendar
from datetime import datetime
from io import BytesIO

from nonebot import logger
from PIL import Image, ImageDraw

from .constants import (
    CALENDAR_BOX_HEIGHT,
    CALENDAR_BOX_WIDTH,
    CALENDAR_IMAGE_WIDTH,
    get_check_image,
    get_deerpipe_image,
    get_font,
)


def generate_calendar(
    now: datetime,
    deer_map: dict[int, int],
    avatar: bytes | None,
) -> bytes:
    """
    生成签到日历图片
    
    Args:
        now: 当前时间
        deer_map: 签到记录 {日期: 签到次数}
        avatar: 用户头像二进制数据
    
    Returns:
        生成的 PNG 图片二进制数据
    """
    try:
        # 获取当月日历
        calendar_weeks = monthcalendar(now.year, now.month)
        
        # 计算图片尺寸
        img_width = CALENDAR_IMAGE_WIDTH
        img_height = CALENDAR_BOX_HEIGHT * (len(calendar_weeks) + 1)
        
        # 创建画布
        img = Image.new("RGBA", (img_width, img_height), "white")
        draw = ImageDraw.Draw(img)
        
        # 获取资源
        font = get_font()
        check_img = get_check_image()
        deerpipe_img = get_deerpipe_image()
        
        # 绘制头像
        if avatar is not None:
            try:
                avatar_img = (
                    Image.open(BytesIO(avatar))
                    .convert("RGBA")
                    .resize((80, 80))
                )
                img.paste(avatar_img, (10, 10))
            except Exception as e:
                logger.warning(f"绘制头像失败: {e}")
        
        # 绘制标题
        title = f"{now.year}-{now.month:02} 签到日历"
        draw.text((100, 10), title, fill="black", font=font)
        
        # 绘制日历
        for week_idx, week in enumerate(calendar_weeks):
            for day_idx, day in enumerate(week):
                if day == 0:
                    continue
                
                x = day_idx * CALENDAR_BOX_WIDTH
                y = (week_idx + 1) * CALENDAR_BOX_HEIGHT
                
                # 绘制鹿管背景
                img.paste(deerpipe_img, (x, y))
                
                # 绘制日期数字
                draw.text(
                    (x + 5, y + CALENDAR_BOX_HEIGHT - 35),
                    str(day),
                    fill="black",
                    font=font,
                )
                
                # 如果该日已签到，绘制勾选标记
                if day in deer_map:
                    img.paste(check_img, (x, y), check_img)
                    
                    # 如果签到次数大于1，显示次数
                    if deer_map[day] > 1:
                        count_text = (
                            "x99+" if deer_map[day] > 99 
                            else f"x{deer_map[day]}"
                        )
                        text_width = draw.textlength(count_text, font=font)
                        draw.text(
                            (x + CALENDAR_BOX_WIDTH - text_width - 5, 
                             y + CALENDAR_BOX_HEIGHT - 35),
                            count_text,
                            fill="red",
                            font=font,
                            stroke_width=1,
                        )
        
        # 输出为 PNG 字节流（不写入磁盘）
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        result = buffer.getvalue()
        
        logger.debug(f"生成日历图片成功，大小: {len(result)} bytes")
        return result
        
    except Exception as e:
        logger.error(f"生成日历图片失败: {e}")
        # 返回一个简单的错误占位图
        return _generate_error_image()


def _generate_error_image() -> bytes:
    """生成错误占位图"""
    img = Image.new("RGBA", (200, 100), "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 40), "图片生成失败", fill="red")
    
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
