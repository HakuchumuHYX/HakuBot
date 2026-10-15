# pjsk_guess_song/services/image_service.py
"""
图像服务：选项图、排行榜、资源版本，均由 templates/ 下的模板经 Chromium 渲染。
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nonebot import logger

from plugins.pjsk_guess_song.config import PluginConfig
from plugins.pjsk_guess_song.services.cache_service import CacheService
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


class ImageService:
    def __init__(
        self,
        cache_service: CacheService,
        resources_dir: Path,
        plugin_version: str,
        config: PluginConfig,
    ):
        self.cache_service = cache_service
        self.resources_dir = resources_dir
        self.plugin_version = plugin_version
        self.config = config

    async def _render(self, name: str, width: int, footer_lines: List[str], **context):
        background = self.resources_dir / "ranking_bg.png"
        footer_lines = [*footer_lines, self.config.custom_footer_text]
        return await render_template(
            TEMPLATE_DIR,
            name,
            width=width,
            bg_uri=background.as_uri() if background.exists() else "",
            footer_lines=[line for line in footer_lines if line],
            **context,
        )

    def _version_line(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"GuessSong v{self.plugin_version} | Generated on {now}"

    async def create_options_image(self, options: List[Dict]) -> Optional[bytes]:
        """12 个歌曲选项的 3x4 图鉴"""
        if not options or len(options) != 12:
            return None
        items = []
        for option in options:
            source = self.cache_service.get_resource_path_or_url(
                f"music_jacket/{option['jacketAssetbundleName']}.png"
            )
            jacket = source.as_uri() if isinstance(source, Path) else source
            items.append({"title": option["title"], "jacket": jacket or ""})
        try:
            return await self._render(
                "options.html", 720, [self._version_line()], options=items
            )
        except Exception as e:
            logger.exception(f"创建选项图片失败: {e}")
            return None

    async def draw_leaderboard_image(
        self, group_name: str, leaderboard_data: List[Tuple[str, int]]
    ) -> Optional[bytes]:
        try:
            return await self._render(
                "leaderboard.html",
                720,
                [self._version_line()],
                group_name=group_name,
                rows=leaderboard_data,
            )
        except Exception as e:
            logger.opt(exception=True).warning(f"生成排行榜图片时出错: {e}")
            return None

    async def draw_resource_version_image(
        self, stats: Dict[str, int], external_info: str
    ) -> Optional[bytes]:
        tracks = [
            ("钢琴", stats.get("piano_count", 0)),
            ("伴奏", stats.get("acc_count", 0)),
            ("人声", stats.get("vocal_count", 0)),
            ("贝斯", stats.get("bass_count", 0)),
            ("鼓组", stats.get("drums_count", 0)),
        ]
        footer_lines = [
            "Masterdata acquired from Team-Haruki",
            "Original resources from sekai.best",
            self._version_line(),
        ]
        try:
            return await self._render(
                "resource.html",
                720,
                footer_lines,
                total=("歌曲", stats.get("song_count", 0)),
                tracks=tracks,
                data_version=external_info,
            )
        except Exception as e:
            logger.opt(exception=True).warning(f"资源版本图片生成失败: {e}")
            return None
