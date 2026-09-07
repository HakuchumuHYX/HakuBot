from pathlib import Path
from datetime import datetime
import base64
from nonebot.log import logger
from utils.rendering.engine import render_html
from jinja2 import Environment, FileSystemLoader
from utils.onebot.avatar import download_avatar
from utils.images.formats import detect_format, MIME_TYPES

from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import AnalysisResult


class ReportRenderer:
    def __init__(self):
        self.template_path = str(
            Path(__file__).parent / "templates" / plugin_config.report_template
        )
        # 初始化 Jinja2 环境
        self.env = Environment(loader=FileSystemLoader(self.template_path))

    async def render_to_image(
        self, analysis_result: AnalysisResult, group_id: str
    ) -> bytes:
        """生成图片报告"""
        render_data = await self._prepare_render_data(analysis_result)

        try:
            # 1. 渲染主模板
            template = self.env.get_template("image_template.html")
            html_content = template.render(**render_data)

            # 2. 转换为图片
            # template_path 用于解析 CSS/JS 等相对路径资源
            # 将路径转换为 file URI 以确保在 Windows 等系统上正确解析
            template_uri = Path(self.template_path).as_uri()

            image_bytes = await render_html(
                html=html_content,
                template_path=template_uri,
                viewport={"width": 800, "height": 10},  # 宽度设为800，高度自适应
            )
            return image_bytes
        except Exception as e:
            logger.error(f"渲染图片失败: {e}")
            raise

    async def _prepare_render_data(self, result: AnalysisResult) -> dict:
        stats = result.statistics

        # 渲染 Topics
        topics_list = [
            {
                "index": i,
                "title": t.topic,
                "detail": t.detail,
                "contributors": "、".join(t.contributors),
            }
            for i, t in enumerate(result.topics, 1)
        ]
        topics_html = self.env.get_template("topic_item.html").render(
            topics=topics_list
        )

        # 渲染 Titles
        titles_list = []
        for t in result.user_titles:
            avatar_data = await self._get_user_avatar(str(t.qq)) if t.qq else None
            titles_list.append(
                {
                    "name": t.name,
                    "title": t.title,
                    "reason": t.reason,
                    "personality": getattr(t, "personality", ""),
                    "avatar_data": avatar_data,
                }
            )
        titles_html = self.env.get_template("user_title_item.html").render(
            titles=titles_list
        )

        # 渲染 Quotes
        quotes_list = []
        for q in result.golden_quotes:
            avatar_url = (
                await self._get_user_avatar(str(q.qq))
                if getattr(q, "qq", None)
                else None
            )
            quotes_list.append(
                {
                    "content": q.content,
                    "sender": q.sender,
                    "reason": q.reason,
                    "avatar_url": avatar_url,
                }
            )
        quotes_html = self.env.get_template("quote_item.html").render(
            quotes=quotes_list
        )

        # 渲染 Chart
        hourly_data = stats.activity_visualization.hourly_activity
        max_count = (
            max(hourly_data.values()) if hourly_data and hourly_data.values() else 1
        )

        template_chart_data = []
        for hour in range(24):
            count = hourly_data.get(hour, 0)
            percentage = (count / max_count) * 100 if max_count > 0 else 0
            template_chart_data.append(
                {"hour": hour, "count": count, "percentage": int(percentage)}
            )

        chart_html = self.env.get_template("activity_chart.html").render(
            chart_data=template_chart_data
        )

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

    async def _get_user_avatar(self, user_id: str) -> str | None:
        if not user_id:
            return None
        try:
            data = await download_avatar(int(user_id), size=100)
            mime = MIME_TYPES[detect_format(data)]
            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception as exc:
            logger.warning(f"头像获取失败: {type(exc).__name__}")
            return None
