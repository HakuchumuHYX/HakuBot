# pjsk_guess_song/services/image_service.py
"""
图像服务
只负责 PIL 的核心图像绘制操作。
已移除 Pilmoji 依赖，使用纯文本符号替代 Emoji。
"""

import asyncio
import time
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from nonebot.log import logger

try:
    from PIL.Image import Resampling

    LANCZOS = Resampling.LANCZOS
except ImportError:
    LANCZOS = 1

from ..config import PluginConfig
from .cache_service import CacheService


class ImageService:
    def __init__(self, cache_service: CacheService, resources_dir: Path, output_dir: Path, plugin_version: str,
                 executor: ThreadPoolExecutor, config: PluginConfig):
        self.cache_service = cache_service
        self.resources_dir = resources_dir
        self.output_dir = output_dir
        self.plugin_version = plugin_version
        self.executor = executor
        self.config = config

    async def create_options_image(self, options: List[Dict]) -> Optional[str]:
        """为12个歌曲选项创建一个3x4的图鉴"""
        if not options or len(options) != 12: return None
        tasks = [self.cache_service.open_image(f"music_jacket/{opt['jacketAssetbundleName']}.png") for opt in options]
        jacket_images = await asyncio.gather(*tasks)
        loop = asyncio.get_running_loop()
        try:
            img_path = await loop.run_in_executor(self.executor, self._draw_options_image_sync, options, jacket_images)
            return img_path
        except Exception as e:
            logger.error(f"在executor中创建选项图片失败: {e}", exc_info=True)
            return None

    def _draw_options_image_sync(self, options: List[Dict], jacket_images: List[Optional[Image.Image]]) -> Optional[
        str]:
        """[同步] 选项图片绘制函数"""
        jacket_w, jacket_h = 140, 140
        padding = 20
        text_h = 50
        cols, rows = 3, 4

        extra_width = 100
        content_area_width = cols * jacket_w + (cols + 1) * padding
        content_width = content_area_width + extra_width
        content_height = rows * (jacket_h + text_h) + (rows + 1) * padding

        watermark_height = 80
        img_w = content_width
        img_h = content_height + watermark_height

        # 创建主画布
        img = Image.new("RGBA", (img_w, img_h), (245, 245, 245, 255))

        # 添加渐变背景
        try:
            bg_color_start, bg_color_end = (230, 240, 255), (200, 210, 240)
            for y in range(img_h):
                r = int(bg_color_start[0] + (bg_color_end[0] - bg_color_start[0]) * y / img_h)
                g = int(bg_color_start[1] + (bg_color_end[1] - bg_color_start[1]) * y / img_h)
                b = int(bg_color_start[2] + (bg_color_end[2] - bg_color_start[2]) * y / img_h)
                draw_bg = ImageDraw.Draw(img)
                draw_bg.line([(0, y), (img_w, y)], fill=(r, g, b, 255))
        except Exception as e:
            logger.warning(f"绘制渐变背景失败: {e}")

        # 添加背景图片（如果存在）
        background_path = self.resources_dir / "ranking_bg.png"
        if background_path.exists():
            try:
                custom_bg = Image.open(background_path).convert("RGBA")
                custom_bg = custom_bg.resize((img_w, img_h), LANCZOS)
                custom_bg.putalpha(128)
                img = Image.alpha_composite(img, custom_bg)
            except Exception as e:
                logger.warning(f"加载或混合自定义背景图片失败: {e}")

        # 添加白色半透明覆盖层
        white_overlay = Image.new("RGBA", (img_w, img_h), (255, 255, 255, 100))
        img = Image.alpha_composite(img, white_overlay)

        # 创建绘图对象
        draw = ImageDraw.Draw(img)

        try:
            font_path = str(self.resources_dir / "font.ttf")
            title_font = ImageFont.truetype(font_path, 16)
            num_font = ImageFont.truetype(font_path, 24)
            id_font = ImageFont.truetype(font_path, 14)
        except IOError:
            logger.warning("未找到字体文件 font.ttf，将使用默认字体。")
            title_font = ImageFont.load_default()
            num_font = ImageFont.load_default()
            id_font = ImageFont.load_default()

        # 调整内容区域居中位置
        content_start_x = (img_w - content_area_width) // 2

        # 绘制选项内容
        for i, option in enumerate(options):
            jacket_img = jacket_images[i]
            if not jacket_img:
                logger.warning(f"未找到歌曲 {option.get('title')} 的封面，跳过绘制。")
                continue

            row_idx, col_idx = i // cols, i % cols
            x = content_start_x + padding + col_idx * (jacket_w + padding)
            y = padding + row_idx * (jacket_h + text_h + padding)

            try:
                # 处理封面图片
                jacket = jacket_img.convert("RGBA").resize((jacket_w, jacket_h), LANCZOS)

                # 创建一个临时画布来绘制封面和编号
                jacket_canvas = Image.new("RGBA", (jacket_w, jacket_h), (0, 0, 0, 0))
                jacket_canvas.paste(jacket, (0, 0))

                # 绘制编号圆圈
                circle_radius = 18
                circle_center = (circle_radius, circle_radius)
                jacket_draw = ImageDraw.Draw(jacket_canvas)
                jacket_draw.ellipse((circle_center[0] - circle_radius, circle_center[1] - circle_radius,
                                     circle_center[0] + circle_radius, circle_center[1] + circle_radius),
                                    fill=(0, 0, 0, 180))

                # 绘制编号
                num_text = f"{i + 1}"

                # 计算文本应该放置的位置，使其在圆圈中心
                bbox = jacket_draw.textbbox((0, 0), num_text, font=num_font)
                text_w = bbox[2] - bbox[0]
                text_h_bbox = bbox[3] - bbox[1]

                text_x = circle_center[0] - text_w / 2
                text_y = circle_center[1] - text_h_bbox / 2 - 2  # 微调

                jacket_draw.text((text_x, text_y), num_text, font=num_font, fill=(255, 255, 255))

                # 将封面合成到主图片
                img.paste(jacket_canvas, (x, y), jacket_canvas)

                # 绘制歌曲标题
                title = option['title']
                if title_font.getbbox(title)[2] > jacket_w:
                    while title_font.getbbox(title + "...")[2] > jacket_w and len(title) > 1:
                        title = title[:-1]
                    title += "..."

                title_bbox = draw.textbbox((0, 0), title, font=title_font)
                title_w = title_bbox[2] - title_bbox[0]
                text_x = x + (jacket_w - title_w) / 2
                text_y = y + jacket_h + 8
                draw.text((text_x, text_y), title, font=title_font, fill=(30, 30, 50))

            except Exception as e:
                logger.error(f"处理歌曲封面失败: {option.get('title')}, 错误: {e}")
                continue

        # 水印部分
        center_x = img_w // 2
        font_color = (80, 90, 120)

        footer_text_1 = f"GuessSong v{self.plugin_version} | Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        footer_text_2 = self.config.custom_footer_text

        footer_y_1 = img_h - 55
        footer_y_2 = img_h - 35

        def draw_centered_text(y, text, font, fill_color):
            bbox = draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            draw.text((center_x - w / 2, y), text, font=font, fill=fill_color)

        draw_centered_text(footer_y_1, footer_text_1, id_font, font_color)

        if footer_text_2:
            draw_centered_text(footer_y_2, footer_text_2, id_font, font_color)

        img_path = self.output_dir / f"song_options_{int(time.time())}.png"
        img.save(img_path)
        return str(img_path)

    async def draw_help_image(self) -> Optional[str]:
        """异步绘制帮助图片。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._draw_help_image_sync)

    def _draw_help_image_sync(self) -> Optional[str]:
        """[同步] 帮助图片绘制函数 (无 Emoji)。"""
        game_modes = {
            'normal': {'name': '普通'}, '1': {'name': '2倍速'}, '2': {'name': '倒放'},
            '3': {'name': 'AI-Assisted Twin Piano ver.'}, '4': {'name': '纯伴奏'},
            '5': {'name': '纯贝斯'}, '6': {'name': '纯鼓组'}, '7': {'name': '纯人声'},
        }
        try:
            width, height = 800, 1350
            # 背景绘制
            bg_color_start, bg_color_end = (230, 240, 255), (200, 210, 240)
            img = Image.new("RGB", (width, height), bg_color_start)
            draw_bg = ImageDraw.Draw(img)
            for y in range(height):
                r = int(bg_color_start[0] + (bg_color_end[0] - bg_color_start[0]) * y / height)
                g = int(bg_color_start[1] + (bg_color_end[1] - bg_color_start[1]) * y / height)
                b = int(bg_color_start[2] + (bg_color_end[2] - bg_color_start[2]) * y / height)
                draw_bg.line([(0, y), (width, y)], fill=(r, g, b))

            background_path = self.resources_dir / "ranking_bg.png"
            if background_path.exists():
                try:
                    custom_bg = Image.open(background_path).convert("RGBA").resize((width, height), LANCZOS)
                    custom_bg.putalpha(128)
                    img = img.convert("RGBA")
                    img = Image.alpha_composite(img, custom_bg)
                except Exception as e:
                    logger.warning(f"加载或混合自定义背景图片失败: {e}")

            if img.mode != 'RGBA': img = img.convert('RGBA')
            white_overlay = Image.new("RGBA", img.size, (255, 255, 255, 100))
            img = Image.alpha_composite(img, white_overlay)

            font_color, shadow_color = (30, 30, 50), (180, 180, 190, 128)
            header_color = (80, 90, 120)

            try:
                font_path = str(self.resources_dir / "font.ttf")
                title_font = ImageFont.truetype(font_path, 48)
                section_font = ImageFont.truetype(font_path, 32)
                body_font = ImageFont.truetype(font_path, 24)
                id_font = ImageFont.truetype(font_path, 16)
                special_font = ImageFont.truetype(font_path, 30)
            except IOError:
                logger.warning("未找到字体文件 font.ttf，将使用默认字体。")
                title_font = ImageFont.load_default()
                section_font = ImageFont.load_default()
                body_font = ImageFont.load_default()
                id_font = ImageFont.load_default()
                special_font = ImageFont.load_default()

            help_text = (
                "--- PJSK猜歌插件帮助 ---\n\n"
                "■ 基础指令\n"
                f"  '猜歌 (1分)' - {game_modes['normal']['name']}\n"
                f"  '猜歌 1 (1分)' - {game_modes['1']['name']}\n"
                f"  '猜歌 2 (3分)' - {game_modes['2']['name']}\n"
                f"  '猜歌 3 (2分)' - {game_modes['3']['name']}\n"
                f"  '猜歌 4 (1分)' - {game_modes['4']['name']}\n"
                f"  '猜歌 5 (3分)' - {game_modes['5']['name']}\n"
                f"  '猜歌 6 (4分)' - {game_modes['6']['name']}\n"
                f"  '猜歌 7 (1分)' - {game_modes['7']['name']}\n\n"
                "■ 高级指令\n"
                "  '随机猜歌 (组合分数)' - 随机组合效果\n"
                "  '猜歌手 (1分)' - 竞猜演唱者\n"
                "  '听 [歌名/ID] <ver>' - 播放指定歌曲\n"
                "    可选择听vs版或sekai版，默认sekai版。\n"
                "  '听<模式> [歌名/ID]' - 播放指定或随机歌曲的特殊音轨。\n"
                "    可用模式: 钢琴, 伴奏, 人声, 贝斯, 鼓组\n"
                "  '听anvo [歌名/ID] [角色名缩写]' - 播放指定或随机的 Another Vocal\n\n"
                "■ 其他功能\n"
                "  '猜歌帮助' - 显示此帮助信息\n"
                "  '猜歌资源' - 显示当前的资源版本\n"
            )

            draw = ImageDraw.Draw(img)

            width, height = img.size
            center_x, current_y = width // 2, 80
            x_margin = 60
            line_height_body = 40
            line_height_section = 55
            lines = help_text.split('\n')
            title_text = lines[0].replace("---", "").strip()

            # 绘制标题 (带阴影)
            bbox = title_font.getbbox(title_text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((center_x - w / 2 + 2, current_y - h / 2 + 2), title_text, font=title_font, fill=shadow_color)
            draw.text((center_x - w / 2, current_y - h / 2), title_text, font=title_font, fill=font_color)

            current_y += 100
            for line in lines[2:]:
                if not line.strip():
                    current_y += line_height_body // 2
                    continue

                if line.startswith("■"):
                    font = section_font
                    y_increment = line_height_section
                else:
                    font = body_font
                    y_increment = line_height_body

                draw.text((x_margin, int(current_y)), line, font=font, fill=font_color)
                current_y += y_increment

            # 水印
            footer_y_1 = height - 60
            footer_text = f"GuessSong v{self.plugin_version} | Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            bbox = id_font.getbbox(footer_text)
            w = bbox[2] - bbox[0]
            draw.text((center_x - w / 2, footer_y_1), footer_text, font=id_font, fill=header_color)

            if self.config.custom_footer_text:
                footer_y_2 = height - 35
                bbox = id_font.getbbox(self.config.custom_footer_text)
                w = bbox[2] - bbox[0]
                draw.text((center_x - w / 2, footer_y_2), self.config.custom_footer_text, font=id_font,
                          fill=header_color)

            img_path = self.output_dir / f"guess_song_help_{int(time.time())}.png"
            img.save(img_path)
            return str(img_path)
        except Exception as e:
            logger.error(f"生成帮助图片时出错: {e}", exc_info=True)
            return None

    async def draw_leaderboard_image(self, group_name: str, leaderboard_data: List[Tuple[str, int]]) -> Optional[str]:
        """[同步] 排行榜图片绘制函数 (无 Emoji)。"""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self.executor,
                self._draw_leaderboard_image_sync,
                group_name,
                leaderboard_data
            )
        except Exception as e:
            logger.error(f"在 executor 中创建排行榜图片失败: {e}", exc_info=True)
            return None

    def _draw_leaderboard_image_sync(self, group_name: str, leaderboard_data: List[Tuple[str, int]]) -> Optional[str]:
        """[同步] 排行榜图片绘制函数。"""
        try:
            base_height = 320
            entry_height = 65
            data_len = len(leaderboard_data)
            width, height = 800, base_height + (data_len * entry_height)

            bg_color_start, bg_color_end = (230, 240, 255), (200, 210, 240)
            img = Image.new("RGB", (width, height), bg_color_start)
            draw_bg = ImageDraw.Draw(img)
            for y in range(height):
                r = int(bg_color_start[0] + (bg_color_end[0] - bg_color_start[0]) * y / height)
                g = int(bg_color_start[1] + (bg_color_end[1] - bg_color_start[1]) * y / height)
                b = int(bg_color_start[2] + (bg_color_end[2] - bg_color_start[2]) * y / height)
                draw_bg.line([(0, y), (width, y)], fill=(r, g, b))

            background_path = self.resources_dir / "ranking_bg.png"
            if background_path.exists():
                try:
                    custom_bg = Image.open(background_path).convert("RGBA").resize((width, height), LANCZOS)
                    custom_bg.putalpha(128)
                    img = img.convert("RGBA")
                    img = Image.alpha_composite(img, custom_bg)
                except Exception as e:
                    logger.warning(f"加载或混合自定义背景图片失败: {e}")

            if img.mode != 'RGBA': img = img.convert('RGBA')
            white_overlay = Image.new("RGBA", img.size, (255, 255, 255, 100))
            img = Image.alpha_composite(img, white_overlay)

            font_color, shadow_color = (30, 30, 50), (180, 180, 190, 128)
            header_color = (80, 90, 120)

            try:
                font_path = str(self.resources_dir / "font.ttf")
                title_font = ImageFont.truetype(font_path, 48)
                header_font = ImageFont.truetype(font_path, 28)
                entry_font = ImageFont.truetype(font_path, 36)
                score_font = ImageFont.truetype(font_path, 36)
                id_font = ImageFont.truetype(font_path, 16)
            except IOError:
                logger.warning("未找到字体文件 font.ttf，将使用默认字体。")
                title_font = ImageFont.load_default()
                header_font = ImageFont.load_default()
                entry_font = ImageFont.load_default()
                score_font = ImageFont.load_default()
                id_font = ImageFont.load_default()

            draw = ImageDraw.Draw(img)

            center_x, current_y = width // 2, 80
            title_text = "群聊猜歌排行"

            # 绘制标题
            bbox = title_font.getbbox(title_text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((center_x - w / 2 + 2, current_y - h / 2 + 2), title_text, font=title_font, fill=shadow_color)
            draw.text((center_x - w / 2, current_y - h / 2), title_text, font=title_font, fill=font_color)

            current_y += 80

            bbox = header_font.getbbox(group_name)
            w = bbox[2] - bbox[0]
            draw.text((center_x - w / 2, current_y), group_name, font=header_font, fill=header_color)

            current_y += 80

            x_margin = 80
            x_name = x_margin + 120
            x_score = width - x_margin
            max_name_width = x_score - x_name - 50

            def get_text_width(text, font):
                bbox = draw.textbbox((0, 0), text, font=font)
                return bbox[2] - bbox[0]

            top_colors = {
                1: (255, 215, 0),
                2: (192, 192, 192),
                3: (205, 127, 50),
            }

            for i, (name, score) in enumerate(leaderboard_data, 1):
                rank_text = f"No.{i}"
                score_text = f"{score} 分"
                rank_color = top_colors.get(i, font_color)

                draw.text((x_margin, int(current_y)), rank_text, font=entry_font, fill=rank_color)

                display_name = name
                name_width = get_text_width(display_name, entry_font)

                while name_width > max_name_width and len(display_name) > 1:
                    display_name = display_name[:-1]
                    name_width = get_text_width(display_name + "...", entry_font)

                if name != display_name:
                    display_name += "..."

                draw.text((x_name, int(current_y)), display_name, font=entry_font, fill=font_color)
                w = get_text_width(score_text, score_font)
                draw.text((x_score - w, int(current_y)), score_text, font=score_font, fill=font_color)

                current_y += 65

            footer_y_1 = height - 60
            footer_text = f"GuessSong v{self.plugin_version} | Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

            bbox = id_font.getbbox(footer_text)
            w = bbox[2] - bbox[0]
            draw.text((center_x - w / 2, footer_y_1), footer_text, font=id_font, fill=header_color)

            if self.config.custom_footer_text:
                footer_y_2 = height - 35
                bbox = id_font.getbbox(self.config.custom_footer_text)
                w = bbox[2] - bbox[0]
                draw.text((center_x - w / 2, footer_y_2), self.config.custom_footer_text, font=id_font,
                          fill=header_color)

            img_path = self.output_dir / f"leaderboard_{int(time.time())}.png"
            img.save(img_path)
            return str(img_path)

        except Exception as e:
            logger.error(f"生成排行榜图片时出错: {e}", exc_info=True)
            return None

    async def draw_resource_version_image(self, stats: Dict[str, int], external_info: str) -> Optional[str]:
        """异步绘制资源版本统计图片 (美化版)"""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self.executor, self._draw_resource_version_image_sync, stats,
                                              external_info)
        except Exception as e:
            logger.error(f"资源版本图片生成失败: {e}", exc_info=True)
            return None

    def _draw_resource_version_image_sync(self, stats: Dict[str, int], external_info: str) -> Optional[str]:
        """[同步] 资源版本图片绘制函数 (重构版：优化布局、修复水印、美化背景)"""
        try:
            # 1. 画布设置
            # 稍微增加一点高度，防止四行水印显得拥挤
            width, height = 800, 920

            # 创建画布
            img = Image.new("RGBA", (width, height), (240, 240, 245, 255))

            # 2. 背景处理
            background_path = self.resources_dir / "ranking_bg.png"
            if background_path.exists():
                try:
                    from PIL import ImageOps
                    custom_bg = Image.open(background_path).convert("RGBA")
                    custom_bg = ImageOps.fit(custom_bg, (width, height), method=LANCZOS, centering=(0.5, 0.5))
                    white_overlay = Image.new("RGBA", (width, height), (255, 255, 255, 80))
                    custom_bg = Image.alpha_composite(custom_bg, white_overlay)
                    img = Image.alpha_composite(img, custom_bg)
                except Exception as e:
                    logger.warning(f"加载背景图片失败: {e}")

            draw = ImageDraw.Draw(img)

            # 3. 字体加载
            font_color = (40, 40, 60)
            secondary_color = (100, 100, 120)

            # 专门定义水印相关的颜色，复刻 _draw_help_image_sync 的 header_color
            watermark_color = (80, 90, 120)

            try:
                font_path = str(self.resources_dir / "font.ttf")
                title_font = ImageFont.truetype(font_path, 46)
                card_label_font = ImageFont.truetype(font_path, 22)
                card_value_font = ImageFont.truetype(font_path, 40)
                version_label_font = ImageFont.truetype(font_path, 20)
                version_val_font = ImageFont.truetype(font_path, 28)
                # 专门加载一个 size=16 的字体用于水印，与猜歌帮助保持一致
                watermark_font = ImageFont.truetype(font_path, 16)
            except IOError:
                title_font = ImageFont.load_default()
                card_label_font = ImageFont.load_default()
                card_value_font = ImageFont.load_default()
                version_label_font = ImageFont.load_default()
                version_val_font = ImageFont.load_default()
                watermark_font = ImageFont.load_default()

            # 4. 绘制大标题
            center_x = width // 2
            title_text = "PJSK猜歌插件资源版本统计"

            bbox = title_font.getbbox(title_text)
            title_w = bbox[2] - bbox[0]
            title_y = 60

            shadow_offset = 2
            draw.text((center_x - title_w / 2 + shadow_offset, title_y + shadow_offset),
                      title_text, font=title_font, fill=(255, 255, 255, 150))
            draw.text((center_x - title_w / 2, title_y), title_text, font=title_font, fill=(30, 30, 50))

            # 5. 绘制统计卡片
            grid_data = [
                ("歌曲总数", f"{stats.get('song_count', 0)}", (100, 149, 237)),
                ("钢琴曲目", f"{stats.get('piano_count', 0)}", (255, 160, 122)),
                ("伴奏音轨", f"{stats.get('acc_count', 0)}", (32, 178, 170)),
                ("人声音轨", f"{stats.get('vocal_count', 0)}", (255, 105, 180)),
                ("贝斯音轨", f"{stats.get('bass_count', 0)}", (147, 112, 219)),
                ("鼓组音轨", f"{stats.get('drums_count', 0)}", (119, 136, 153))
            ]

            start_y = 160
            card_width = 320
            card_height = 110
            gap_x = 40
            gap_y = 30

            grid_total_width = (card_width * 2) + gap_x
            start_x = (width - grid_total_width) // 2

            for i, (label, value, color) in enumerate(grid_data):
                row = i // 2
                col = i % 2
                x = start_x + col * (card_width + gap_x)
                y = start_y + row * (card_height + gap_y)

                draw.rounded_rectangle([x, y, x + card_width, y + card_height], radius=15, fill=(255, 255, 255, 210))
                bar_width = 8
                draw.rounded_rectangle([x, y, x + bar_width + 5, y + card_height], radius=15, fill=color)
                draw.rectangle([x + bar_width, y, x + bar_width + 5, y + card_height], fill=(255, 255, 255, 210))
                draw.rectangle([x + 5, y, x + bar_width, y + card_height], fill=color)

                text_start_x = x + 35
                card_center_y = y + card_height / 2
                draw.text((text_start_x, card_center_y - 25), label, font=card_label_font, fill=secondary_color,
                          anchor="lm")
                draw.text((text_start_x, card_center_y + 15), value, font=card_value_font, fill=font_color, anchor="lm")

            # 6. DataVersion 区域
            version_area_y = start_y + 3 * (card_height + gap_y) + 20
            dv_label = "Current Data Version"
            dv_val = external_info

            lbl_w = version_label_font.getbbox(dv_label)[2]
            val_w = version_val_font.getbbox(dv_val)[2]
            max_text_w = max(lbl_w, val_w) + 60
            capsule_h = 90
            capsule_x1 = center_x - max_text_w // 2
            capsule_y1 = version_area_y
            capsule_x2 = center_x + max_text_w // 2
            capsule_y2 = version_area_y + capsule_h

            draw.rounded_rectangle([capsule_x1, capsule_y1, capsule_x2, capsule_y2], radius=20,
                                   fill=(255, 255, 255, 180))
            draw.text((center_x, capsule_y1 + 25), dv_label, font=version_label_font, fill=(70, 130, 180), anchor="mm")
            draw.text((center_x, capsule_y1 + 60), dv_val, font=version_val_font, fill=(30, 30, 30), anchor="mm")

            # -------------------------------------------------------------------------
            # 7. 底部水印 (复刻猜歌帮助样式 + 自定义水印)
            # -------------------------------------------------------------------------
            # 构造水印内容列表
            watermark_lines = [
                "Masterdata acquired from Team-Haruki",
                "Original resources from sekai.best",
                f"GuessSong v{self.plugin_version} | Generated on {datetime.now().strftime('%Y-%m-%d')}"
            ]

            # 如果配置了自定义水印，追加到列表最后（显示在最下方）
            if self.config.custom_footer_text:
                watermark_lines.append(self.config.custom_footer_text)

            # 动态绘制逻辑：从底部向上堆叠
            # 猜歌帮助中最后一行 y = height - 35，倒数第二行 y = height - 60 (间距25)
            line_step = 25
            base_y = height - 35  # 最底下一行的基准位置

            for i, text in enumerate(reversed(watermark_lines)):
                # i=0 是最后一行，i=1 是倒数第二行...
                current_y = base_y - (i * line_step)

                bbox = watermark_font.getbbox(text)
                w = bbox[2] - bbox[0]
                # 绘制文字（水平居中）
                draw.text((center_x - w / 2, current_y), text, font=watermark_font, fill=watermark_color)

            # 保存
            img_path = self.output_dir / f"resource_version_{int(time.time())}.png"
            img.save(img_path)
            return str(img_path)

        except Exception as e:
            logger.error(f"资源版本图片生成失败: {e}", exc_info=True)
            return None
