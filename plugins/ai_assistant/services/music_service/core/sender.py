from __future__ import annotations

import base64
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.log import logger

from ..types import MusicServiceConfig
from .downloader import Downloader
from .model import Song
from .platform import BaseMusicPlayer
from .renderer import MusicRenderer


@dataclass(slots=True)
class SendContext:
    bot: Bot
    event: MessageEvent

    @property
    def user_id(self) -> str:
        return str(self.event.get_user_id())

    @property
    def group_id(self) -> Optional[str]:
        if isinstance(self.event, GroupMessageEvent):
            return str(self.event.group_id)
        return None


class MusicSender:
    def __init__(self, cfg: MusicServiceConfig, renderer: MusicRenderer, downloader: Downloader):
        self.cfg = cfg
        self.renderer = renderer
        self.downloader = downloader

    @staticmethod
    def _format_time(duration_ms: int | None) -> str:
        if not duration_ms:
            return "??:??"
        duration = duration_ms // 1000
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        seconds = duration % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    async def send_song_selection(
        self,
        ctx: SendContext,
        songs: list[Song],
        title: str | None = None,
    ) -> Optional[int]:
        """
        发送歌曲选择列表，返回 message_id（若能获取）。
        """
        formatted_songs = [f"{i + 1}. {s.name} - {s.artists}" for i, s in enumerate(songs)]
        if title:
            formatted_songs.insert(0, title)
        msg = "\n".join(formatted_songs)

        try:
            ret = await ctx.bot.send(ctx.event, msg)
            # OneBot v11: 可能返回 int / dict
            if isinstance(ret, int):
                return ret
            if isinstance(ret, dict) and "message_id" in ret:
                return int(ret["message_id"])
        except Exception as e:
            logger.warning(f"[music_plugin] send_song_selection failed: {e}")
        return None

    async def send_comment(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """发热评（失败不影响主流程）"""
        if not song.comments:
            await player.fetch_comments(song)
        if not song.comments:
            return False

        try:
            content = random.choice(song.comments).get("content")
            if content:
                await ctx.bot.send(ctx.event, str(content))
                return True
        except Exception:
            return False
        return False

    async def send_lyrics(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """发歌词图（失败不影响主流程）"""
        if not song.lyrics:
            await player.fetch_lyrics(song)
        if not song.lyrics:
            logger.warning(f"[music_plugin] lyrics empty: {song.name}")
            return False

        try:
            img = self.renderer.draw_lyrics(song.lyrics)
            b64 = base64.b64encode(img).decode("ascii")
            seg = MessageSegment.image(f"base64://{b64}")
            await ctx.bot.send(ctx.event, seg)
            return True
        except Exception as e:
            logger.warning(f"[music_plugin] send_lyrics failed: {e}")
            return False

    async def send_card(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """
        发音乐卡片（OneBot v11 的 music segment）。
        原插件：仅 QQ + 网易云时可用；在 HakuBot（OneBot v11）等价为“仅网易云播放器启用”。
        """
        if player.platform.name not in {"netease", "netease_nodejs"}:
            return False

        try:
            seg = MessageSegment(type="music", data={"type": "163", "id": str(song.id)})
            await ctx.bot.send(ctx.event, seg)
            return True
        except Exception as e:
            logger.warning(f"[music_plugin] send_card failed: {e}")
            return False

    async def send_record(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """发语音"""
        if not song.audio_url:
            song = await player.fetch_extra(song)
        if not song.audio_url:
            await ctx.bot.send(ctx.event, f"【{song.name}】音频获取失败")
            return False

        try:
            seg = MessageSegment.record(song.audio_url)
            await ctx.bot.send(ctx.event, seg)
            return True
        except Exception as e:
            logger.warning(f"[music_plugin] send_record failed: {e}")
            return False

    async def send_file(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """发文件：下载后调用 OneBot 上传文件接口"""
        if not song.audio_url:
            song = await player.fetch_extra(song)
        if not song.audio_url:
            await ctx.bot.send(ctx.event, f"【{song.name}】音频获取失败")
            return False

        file_path = await self.downloader.download_song(song.audio_url)
        if not file_path:
            await ctx.bot.send(ctx.event, f"【{song.name}】音频文件下载失败")
            return False

        file_name = f"{song.name}_{song.artists}{Path(file_path).suffix}"

        try:
            if isinstance(ctx.event, GroupMessageEvent):
                await ctx.bot.call_api(
                    "upload_group_file",
                    group_id=ctx.event.group_id,
                    file=str(file_path),
                    name=file_name,
                )
            else:
                await ctx.bot.call_api(
                    "upload_private_file",
                    user_id=int(ctx.user_id),
                    file=str(file_path),
                    name=file_name,
                )
            return True
        except Exception as e:
            logger.warning(f"[music_plugin] send_file upload failed: {e}")
            return False

    async def send_text(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """发文本"""
        try:
            song = await player.fetch_extra(song)
            info = f"🎶{song.name} - {song.artists} {self._format_time(song.duration)}\n{song.to_lines()}"
            await ctx.bot.send(ctx.event, info)
            return True
        except Exception as e:
            logger.warning(f"[music_plugin] send_text failed: {e}")
            return False

    async def send_song(self, ctx: SendContext, player: BaseMusicPlayer, song: Song) -> bool:
        """
        按 send_modes 降级发送歌曲；成功后附加热评/歌词（可选）。
        返回：是否成功发送主内容（任一模式成功即 True）。
        """
        logger.debug(
            f"[music_plugin] {ctx.user_id} 点歌：{player.platform.display_name} -> {song.name}_{song.artists}"
        )

        mode_funcs = {
            "card": self.send_card,
            "record": self.send_record,
            "file": self.send_file,
            "text": self.send_text,
        }

        sent = False
        for mode in self.cfg.send_modes:
            fn = mode_funcs.get(mode)
            if not fn:
                continue

            try:
                ok = await fn(ctx, player, song)
            except Exception as e:
                logger.warning(f"[music_plugin] {mode} send exception: {e}")
                ok = False

            if ok:
                sent = True
                break

        if not sent:
            await ctx.bot.send(ctx.event, "歌曲发送失败")
            return False

        # 附加内容不影响主流程
        if self.cfg.enable_comments:
            try:
                await self.send_comment(ctx, player, song)
            except Exception:
                pass

        if self.cfg.enable_lyrics:
            try:
                await self.send_lyrics(ctx, player, song)
            except Exception:
                pass

        return True
