from pathlib import Path
from datetime import datetime
import asyncio
import base64
import aiohttp
from aiohttp import ClientTimeout
from nonebot.log import logger
from ....utils.browser import html_to_pic
from jinja2 import Environment, FileSystemLoader

from ..config import plugin_config
from ..models import AnalysisResult


class ReportRenderer:
    def __init__(self):
        self.template_path = str(
            Path(__file__).parent / "templates" / plugin_config.report_template
        )
        # 初始化 Jinja2 环境
        self.env = Environment(loader=FileSystemLoader(self.template_path))

        # 简单内存缓存：减少同一次渲染过程里的重复请求
        # key: user_id -> data_uri
        self._avatar_cache: dict[str, str] = {}

    async def render_to_image(self, analysis_result: AnalysisResult, group_id: str) -> bytes:
        """生成图片报告"""
        timeout = ClientTimeout(total=6)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            render_data = await self._prepare_render_data(analysis_result, session=session)

        try:
            # 1. 渲染主模板
            template = self.env.get_template("image_template.html")
            html_content = template.render(**render_data)

            # 2. 转换为图片
            # template_path 用于解析 CSS/JS 等相对路径资源
            # 将路径转换为 file URI 以确保在 Windows 等系统上正确解析
            template_uri = Path(self.template_path).as_uri()

            image_bytes = await html_to_pic(
                html=html_content,
                template_path=template_uri,
                viewport={"width": 800, "height": 10},  # 宽度设为800，高度自适应
            )
            return image_bytes
        except Exception as e:
            logger.error(f"渲染图片失败: {e}")
            raise

    async def _prepare_render_data(self, result: AnalysisResult, session: aiohttp.ClientSession) -> dict:
        stats = result.statistics

        # 渲染 Topics
        # 兼容模板：目前 topic_item.html 使用 topic.topic.topic / topic.topic.detail（topic 是 SummaryTopic 对象）
        topics_list = [
            {
                "index": i,
                "topic": t,  # SummaryTopic
                "topic_title": t.topic,  # 额外字段：方便未来模板直接使用
                "detail": t.detail,
                "contributors": "、".join(t.contributors),
            }
            for i, t in enumerate(result.topics, 1)
        ]
        topics_html = self.env.get_template("topic_item.html").render(topics=topics_list)

        # 渲染 Titles
        titles_list = []
        for t in result.user_titles:
            avatar_data = (
                await self._get_user_avatar(session, str(t.qq)) if t.qq else None
            )
            titles_list.append(
                {
                    "name": t.name,
                    "title": t.title,
                    "mbti": t.mbti,
                    "reason": t.reason,
                    "avatar_data": avatar_data,
                }
            )
        titles_html = self.env.get_template("user_title_item.html").render(titles=titles_list)

        # 渲染 Quotes
        # 兼容：优先使用 result.golden_quotes（新结构）；若为空则回退到 stats.golden_quotes（旧结构）
        quotes_src = (
            result.golden_quotes if getattr(result, "golden_quotes", None) else stats.golden_quotes
        )

        quotes_list = []
        for q in quotes_src:
            avatar_url = (
                await self._get_user_avatar(session, str(q.qq)) if getattr(q, "qq", None) else None
            )
            quotes_list.append(
                {
                    "content": q.content,
                    "sender": q.sender,
                    "reason": q.reason,
                    "avatar_url": avatar_url,
                }
            )
        quotes_html = self.env.get_template("quote_item.html").render(quotes=quotes_list)

        # 渲染 Chart
        hourly_data = stats.activity_visualization.hourly_activity
        max_count = max(hourly_data.values()) if hourly_data and hourly_data.values() else 1

        template_chart_data = []
        for hour in range(24):
            count = hourly_data.get(hour, 0)
            percentage = (count / max_count) * 100 if max_count > 0 else 0
            template_chart_data.append({"hour": hour, "count": count, "percentage": int(percentage)})

        chart_html = self.env.get_template("activity_chart.html").render(chart_data=template_chart_data)

        return {
            "current_date": datetime.now().strftime("%Y年%m月%d日"),
            "current_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": stats.message_count,
            "participant_count": stats.participant_count,
            "total_characters": stats.total_characters,
            "emoji_count": stats.emoji_count,
            "most_active_period": stats.most_active_period,
            "topics_html": topics_html,
            "titles_html": titles_html,
            "quotes_html": quotes_html,
            "hourly_chart_html": chart_html,
            "total_tokens": stats.token_usage.total_tokens,
            "prompt_tokens": stats.token_usage.prompt_tokens,
            "completion_tokens": stats.token_usage.completion_tokens,
            "watermark_text": plugin_config.watermark_text,
        }

    async def _get_user_avatar(
        self, 
        session: aiohttp.ClientSession, 
        user_id: str,
        max_retries: int = 2,
    ) -> str | None:
        """
        获取用户头像 Base64（带缓存、复用 session、重试机制）
        
        Args:
            session: aiohttp 会话
            user_id: 用户 QQ 号
            max_retries: 最大重试次数
        
        Returns:
            头像的 data URI，失败时返回 None
        """
        if not user_id:
            return None
        
        cached = self._avatar_cache.get(user_id)
        if cached:
            return cached

        # 多个头像 CDN 备选
        avatar_urls = [
            f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=100",
            f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100",
            f"https://q2.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=100",
        ]
        
        for url in avatar_urls:
            for attempt in range(max_retries):
                try:
                    async with session.get(url, timeout=ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            # 验证是否为有效图片（至少有一些数据）
                            if len(data) > 100:
                                b64 = base64.b64encode(data).decode()
                                data_uri = f"data:image/jpeg;base64,{b64}"
                                self._avatar_cache[user_id] = data_uri
                                return data_uri
                except asyncio.TimeoutError:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                        continue
                except Exception as e:
                    logger.debug(f"获取头像失败 (URL={url}, 尝试={attempt+1}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                        continue
                break  # 当前 URL 失败，尝试下一个
        
        logger.warning(f"获取用户 {user_id} 头像失败，所有 CDN 均不可用")
        return None
