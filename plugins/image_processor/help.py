# help.py
import os
import tempfile
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import textwrap


async def generate_help_image() -> str:
    """生成帮助信息图片"""
    try:
        # 帮助文本内容
        help_text = """
图片处理插件使用说明

基本用法：回复图片消息并使用以下命令

GIF 处理命令：
• img倒放 - GIF倒放效果
• imgx [倍数] - GIF倍速播放 (例如：imgx 2)

图片处理命令：
• imgcut - 移除图片背景（抠图）
• img对称 / img左对称 - 图片左对称效果
• img右对称 - 图片右对称效果
• img中心对称 - 图片中心对称效果
• img上对称 - 图片上对称效果
• img下对称 - 图片下对称效果

视频处理命令：
• imggif - 将视频转换为GIF（最长60秒）

帮助命令：
• imghelp - 显示此帮助信息

使用提示：
1. 所有命令都需要回复图片消息使用
2. GIF相关命令仅对GIF图片有效
3. 倍速倍数范围为 0.1-5，超过5会自动调整为5
4. 处理时间取决于图片大小和复杂度，请耐心等待
5. 视频转GIF功能支持最长60秒的视频
6. 转换时间取决于视频长度，请耐心等待

如遇问题，请检查：
• 是否回复了正确的图片消息
• 图片格式是否受支持
• 网络连接是否正常
"""

        # 创建图片
        img_width = 800
        line_height = 30
        margin = 40

        # 计算图片高度
        lines = help_text.strip().split('\n')
        img_height = margin * 2 + len(lines) * line_height

        # 创建白色背景图片
        img = Image.new('RGB', (img_width, img_height), color='white')
        draw = ImageDraw.Draw(img)

        # 尝试加载字体
        try:
            # 尝试使用系统字体
            font = ImageFont.truetype("msyh.ttc", 20)  # 微软雅黑
        except:
            try:
                font = ImageFont.truetype("simhei.ttf", 20)  # 黑体
            except:
                try:
                    font = ImageFont.truetype("arial.ttf", 20)  # Arial
                except:
                    font = ImageFont.load_default()  # 默认字体

        # 绘制文本
        y = margin
        for line in lines:
            if line.strip():
                # 根据行内容设置颜色
                if '使用说明' in line:
                    color = 'darkblue'
                    # 尝试使用更大的字体显示标题
                    try:
                        title_font = ImageFont.truetype("msyh.ttc", 24) if 'msyh.ttc' in str(font) else font
                        draw.text((margin, y), line.strip(), fill=color, font=title_font)
                    except:
                        draw.text((margin, y), line.strip(), fill=color, font=font)
                elif '处理命令' in line or '提示' in line or '技术支持' in line or '帮助命令' in line:
                    color = 'darkgreen'
                    draw.text((margin, y), line.strip(), fill=color, font=font)
                elif line.strip().startswith('•'):
                    color = 'black'
                    # 缩进命令
                    draw.text((margin + 20, y), line.strip(), fill=color, font=font)
                elif line.strip().startswith('1.') or line.strip().startswith('2.') or line.strip().startswith(
                        '3.') or line.strip().startswith('4.'):
                    color = 'gray'
                    draw.text((margin + 20, y), line.strip(), fill=color, font=font)
                else:
                    color = 'darkred'
                    draw.text((margin, y), line.strip(), fill=color, font=font)
            y += line_height

        # 添加边框
        draw.rectangle([5, 5, img_width - 5, img_height - 5], outline='lightgray', width=2)

        # 保存图片
        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_help"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"help_{os.urandom(4).hex()}.png"

        img.save(str(output_path), 'PNG', quality=95)

        return str(output_path)

    except Exception as e:
        print(f"生成帮助图片时出错: {e}")
        # 如果生成图片失败，返回空字符串，让调用者处理
        return ""


async def get_help_text() -> str:
    """获取纯文本帮助信息（备用）"""
    help_text = """
图片处理插件使用说明

基本用法：回复图片消息并使用以下命令

GIF 处理命令：
• img倒放 - GIF倒放效果
• imgx [倍数] - GIF倍速播放 (例如：imgx 2)

图片处理命令：
• imgcut - 移除图片背景（抠图）
• img对称 / img左对称 - 图片左对称效果
• img右对称 - 图片右对称效果
• img中心对称 - 图片中心对称效果
• img上对称 - 图片上对称效果
• img下对称 - 图片下对称效果

帮助命令：
• imghelp - 显示此帮助信息

视频处理命令：
• imggif - 将视频转换为GIF（最长60秒）

使用提示：
1. 所有命令都需要回复图片消息使用
2. GIF相关命令仅对GIF图片有效
3. 倍速倍数范围为 0.1-5，超过5会自动调整为5
4. 处理时间取决于图片大小和复杂度，请耐心等待
5. 视频转GIF功能支持最长60秒的视频
6. 转换时间取决于视频长度，请耐心等待

如遇问题，请检查：
• 是否回复了正确的图片消息
• 图片格式是否受支持
• 网络连接是否正常
"""
    return help_text
