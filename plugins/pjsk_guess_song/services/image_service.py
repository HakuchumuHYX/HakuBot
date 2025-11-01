# pjsk_guess_song/services/image_service.py
"""
(新文件)
图像服务
只负责 PIL 和 Pilmoji 的核心图像绘制操作。
"""

import asyncio
import time
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

try:
    from PIL.Image import Resampling

    LANCZOS = Resampling.LANCZOS
except ImportError:
    LANCZOS = 1

try:
    from pilmoji import Pilmoji
except (ImportError, AttributeError) as e:
    Pilmoji = None
    print(f"Pilmoji import failed ({type(e).__name__}), emoji rendering will be disabled. Error: {e}")

from nonebot.log import logger
from .cache_service import CacheService
# --- [修改] ---
from ..config import PluginConfig  # 导入配置模型


# --- [修改] 结束 ---


class ImageService:
    # --- [修改] ---
    def __init__(self, cache_service: CacheService, resources_dir: Path, output_dir: Path, plugin_version: str,
                 executor: ThreadPoolExecutor, config: PluginConfig):  # 添加 config
        self.cache_service = cache_service
        self.resources_dir = resources_dir
        self.output_dir = output_dir
        self.plugin_version = plugin_version
        self.executor = executor
        self.config = config  # 存储 config 对象

    # --- [修改] 结束 ---

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
        jacket_w, jacket_h = 128, 128
        padding = 15
        text_h = 50
        cols, rows = 3, 4
        img_w = cols * jacket_w + (cols + 1) * padding
        img_h = rows * (jacket_h + text_h) + (rows + 1) * padding
        img = Image.new('RGBA', (img_w, img_h), (245, 245, 245, 255))
        try:
            font_path = str(self.resources_dir / "font.ttf")
            title_font = ImageFont.truetype(font_path, 16)
            num_font = ImageFont.truetype(font_path, 22)
        except IOError:
            logger.warning("未找到字体文件 font.ttf，将使用默认字体。")
            title_font = ImageFont.load_default()
            num_font = ImageFont.load_default()

        draw = ImageDraw.Draw(img)
        for i, option in enumerate(options):
            jacket_img = jacket_images[i]
            if not jacket_img:
                logger.warning(f"未找到歌曲 {option.get('title')} 的封面，跳过绘制。")
                continue

            row_idx, col_idx = i // cols, i % cols
            x = padding + col_idx * (jacket_w + padding)
            y = padding + row_idx * (jacket_h + text_h + padding)
            try:
                jacket = jacket_img.convert("RGBA").resize((jacket_w, jacket_h), LANCZOS)
                img.paste(jacket, (x, y), jacket)
                num_text = f"{i + 1}"
                circle_radius = 16
                circle_center = (x + circle_radius, y + circle_radius)
                draw.ellipse((circle_center[0] - circle_radius, circle_center[1] - circle_radius,
                              circle_center[0] + circle_radius, circle_center[1] + circle_radius),
                             fill=(0, 0, 0, 180))

                if Pilmoji:
                    with Pilmoji(img) as pilmoji_drawer:
                        pilmoji_drawer.text(circle_center, num_text, font=num_font, fill=(255, 255, 255), anchor="mm")
                else:
                    text_bbox = draw.textbbox((0, 0), num_text, font=num_font)
                    text_w = text_bbox[2] - text_bbox[0]
                    text_h = text_bbox[3] - text_bbox[1]
                    text_x = circle_center[0] - text_w / 2
                    text_y = circle_center[1] - text_h / 2
                    draw.text((text_x, text_y), num_text, font=num_font, fill=(255, 255, 255))

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
        img_path = self.output_dir / f"song_options_{int(time.time())}.png"
        img.save(img_path)
        return str(img_path)

    async def draw_help_image(self) -> Optional[str]:
        """异步绘制帮助图片。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._draw_help_image_sync)

    def _draw_help_image_sync(self) -> Optional[str]:
        """[同步] 帮助图片绘制函数。"""
        game_modes = {
            'normal': {'name': '普通'}, '1': {'name': '2倍速'}, '2': {'name': '倒放'},
            '3': {'name': 'AI-Assisted Twin Piano ver.'}, '4': {'name': '纯伴奏'},
            '5': {'name': '纯贝斯'}, '6': {'name': '纯鼓组'}, '7': {'name': '纯人声'},
        }
        try:
            width, height = 800, 1350
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
                title_font = ImageFont.load_default(size=48)
                section_font = ImageFont.load_default(size=32)
                body_font = ImageFont.load_default(size=24)
                id_font = ImageFont.load_default(size=16)
                special_font = ImageFont.load_default(size=30)

            help_text = (
                "--- PJSK猜歌插件帮助 ---\n\n"
                "🎵 基础指令\n"
                f"  `猜歌` - {game_modes['normal']['name']}\n"
                f"  `猜歌 1` - {game_modes['1']['name']}\n"
                f"  `猜歌 2` - {game_modes['2']['name']}\n"
                f"  `猜歌 3` - {game_modes['3']['name']}\n"
                f"  `猜歌 4` - {game_modes['4']['name']}\n"
                f"  `猜歌 5` - {game_modes['5']['name']}\n"
                f"  `猜歌 6` - {game_modes['6']['name']}\n"
                f"  `猜歌 7` - {game_modes['7']['name']}\n\n"
                "🎲 高级指令\n"
                "  `随机猜歌` - 随机组合效果\n"
                "  `猜歌手` - 竞猜演唱者\n"
                "  `听<模式> [歌名/ID]` - 播放指定或随机歌曲的特殊音轨。\n"
                "    可用模式: 钢琴, 伴奏, 人声, 贝斯, 鼓组\n"
                "  `听anvo [歌名/ID] [角色名缩写]` - 播放指定或随机的 Another Vocal\n\n"
                "📊 其他功能\n"
                "  `猜歌帮助` - 显示此帮助信息\n"
            )

            if Pilmoji:
                with Pilmoji(img) as pilmoji:
                    # --- [修改] ---
                    self._draw_help_text(pilmoji.text, img, title_font, section_font, body_font, id_font, special_font,
                                         help_text, font_color, shadow_color, header_color, self.config)  # 传入 config
                    # --- [修改] 结束 ---
            else:
                draw = ImageDraw.Draw(img)
                # --- [修改] ---
                self._draw_help_text(draw.text, img, title_font, section_font, body_font, id_font, special_font,
                                     help_text, font_color, shadow_color, header_color, self.config)  # 传入 config
                # --- [修改] 结束 ---

            img_path = self.output_dir / f"guess_song_help_{int(time.time())}.png"
            img.save(img_path)
            return str(img_path)
        except Exception as e:
            logger.error(f"生成帮助图片时出错: {e}", exc_info=True)
            return None

    # --- [修改] ---
    def _draw_help_text(self, draw_func, img, title_font, section_font, body_font, id_font, special_font, help_text,
                        font_color, shadow_color, header_color, config: PluginConfig):  # 添加 config 参数
        # --- [修改] 结束 ---
        """[同步] 帮助图片绘制的辅助函数，用于兼容 Pilmoji"""
        width, height = img.size
        center_x, current_y = width // 2, 80
        x_margin = 60
        line_height_body = 40
        line_height_section = 55
        lines = help_text.split('\n')
        title_text = lines[0].replace("---", "").strip()

        try:
            draw_func((int(center_x) + 2, int(current_y) + 2), title_text, font=title_font, fill=shadow_color,
                      anchor="mm")
        except TypeError:
            bbox = title_font.getbbox(title_text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw_func((int(center_x) - w / 2 + 2, int(current_y) - h / 2 + 2), title_text, font=title_font,
                      fill=shadow_color)
        try:
            draw_func((int(center_x), int(current_y)), title_text, font=title_font, fill=font_color, anchor="mm")
        except TypeError:
            bbox = title_font.getbbox(title_text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw_func((int(center_x) - w / 2, int(current_y) - h / 2), title_text, font=title_font, fill=font_color)

        current_y += 100
        for line in lines[2:]:
            if not line.strip():
                current_y += line_height_body // 2
                continue

            is_special_line = False
            if is_special_line:
                font = special_font
                y_increment = line_height_section
                text_to_draw = line.strip()
            elif line.startswith("🎵") or line.startswith("🎲") or line.startswith("📊"):
                font = section_font
                y_increment = line_height_section
                text_to_draw = line.strip()
            else:
                font = body_font
                y_increment = line_height_body
                text_to_draw = line

            draw_func((x_margin, int(current_y)), text_to_draw, font=font, fill=font_color)
            current_y += y_increment

        # --- [修改] ---
        # 调整第一行水印 Y 轴
        footer_y_1 = height - 60  # 原 40
        footer_y_2 = height - 35  # 新增
        # --- [修改] 结束 ---

        footer_text = f"GuessSong v{self.plugin_version} | Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        try:
            draw_func((int(center_x), footer_y_1), footer_text, font=id_font, fill=header_color, anchor="ms")
        except TypeError:
            bbox = id_font.getbbox(footer_text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw_func((int(center_x) - w / 2, footer_y_1 - h), footer_text, font=id_font, fill=header_color)

        # --- [新功能] ---
        # 绘制第二行自定义水印
        custom_footer = config.custom_footer_text
        if custom_footer:
            try:
                draw_func((int(center_x), footer_y_2), custom_footer, font=id_font, fill=header_color, anchor="ms")
            except TypeError:
                bbox = id_font.getbbox(custom_footer)
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                draw_func((int(center_x) - w / 2, footer_y_2 - h), custom_footer, font=id_font, fill=header_color)
        # --- [新功能] 结束 ---

    async def draw_leaderboard_image(self, group_name: str, leaderboard_data: List[Tuple[str, int]]) -> Optional[str]:
        """[新功能] 异步绘制排行榜图片。"""
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
        """[新功能][同步] 排行榜图片绘制函数。"""
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
                title_font = ImageFont.load_default(size=48)
                header_font = ImageFont.load_default(size=28)
                entry_font = ImageFont.load_default(size=36)
                score_font = ImageFont.load_default(size=36)
                id_font = ImageFont.load_default(size=16)

            draw = ImageDraw.Draw(img)
            if Pilmoji:
                draw = Pilmoji(img)

            center_x, current_y = width // 2, 80
            title_text = "群聊猜歌排行"

            def draw_text_centered(y, text, font, fill_color, shadow_fill=None):
                try:
                    if shadow_fill:
                        draw.text((int(center_x) + 2, int(y) + 2), text, font=font, fill=shadow_fill, anchor="mm")
                    draw.text((int(center_x), int(y)), text, font=font, fill=fill_color, anchor="mm")
                except TypeError:
                    bbox = font.getbbox(text)
                    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                    if shadow_fill:
                        draw.text((int(center_x) - w / 2 + 2, int(y) - h / 2 + 2), text, font=font, fill=shadow_fill)
                    draw.text((int(center_x) - w / 2, int(y) - h / 2), text, font=font, fill=fill_color)

            draw_text_centered(current_y, title_text, title_font, font_color, shadow_color)
            current_y += 80
            draw_text_centered(current_y, group_name, header_font, header_color)
            current_y += 80

            x_margin = 80
            x_name = x_margin + 120
            x_score = width - x_margin
            max_name_width = x_score - x_name - 50

            def get_text_width(text, font):
                try:
                    bbox = draw.textbbox((0, 0), text, font=font)
                    return bbox[2] - bbox[0]
                except Exception:
                    try:
                        bbox = font.getbbox(text)
                        return bbox[2] - bbox[0]
                    except Exception:
                        return len(text) * (font.size // 2)

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

            # --- [修改] ---
            # 调整第一行水印 Y 轴
            footer_y_1 = height - 60  # 原 40
            footer_y_2 = height - 35  # 新增
            # --- [修改] 结束 ---

            footer_text = f"GuessSong v{self.plugin_version} | Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            draw_text_centered(footer_y_1, footer_text, id_font, header_color)

            # --- [新功能] ---
            # 绘制第二行自定义水印
            custom_footer = self.config.custom_footer_text
            if custom_footer:
                draw_text_centered(footer_y_2, custom_footer, id_font, header_color)
            # --- [新功能] 结束 ---

            img_path = self.output_dir / f"leaderboard_{int(time.time())}.png"
            img.convert("RGB").save(img_path)
            return str(img_path)

        except Exception as e:
            logger.error(f"生成排行榜图片时出错: {e}", exc_info=True)
            return None