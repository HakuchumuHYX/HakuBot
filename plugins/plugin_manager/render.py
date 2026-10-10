from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(slots=True)
class PluginStatusRow:
    """A single display row: parent plugin or sub-feature."""

    name: str
    plugin_id: str
    enabled: bool
    is_child: bool = False


@dataclass(slots=True)
class PluginStatusGroup:
    """A card representing a parent plugin with optional child feature rows."""

    parent: PluginStatusRow
    children: list[PluginStatusRow]


async def render_plugin_status_image(
    groups: Sequence[PluginStatusGroup],
    group_id: str,
    title: str = "插件开关状态",
    watermark_text: str = "",
    watermark_position: str = "bottom_right",
) -> bytes:
    """Render plugin switches as PNG bytes."""
    rows = [row for g in groups for row in (g.parent, *g.children)]
    enabled_count = sum(row.enabled for row in rows)
    return await render_template(
        TEMPLATE_DIR,
        "status.html",
        width=980,
        title=title,
        group_id=group_id,
        groups=groups,
        # 插件多时分两列，避免图片过长
        columns=2 if len(groups) > 8 else 1,
        enabled_count=enabled_count,
        disabled_count=len(rows) - enabled_count,
        watermark_text=watermark_text.strip(),
        watermark_position=watermark_position,
    )
