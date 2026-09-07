"""Existing light-blue and dark card palettes, shared by PIL and CSS."""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

ThemeMode = Literal["auto", "light", "dark"]


@dataclass(frozen=True)
class RenderTheme:
    name: str
    canvas_bg: tuple
    card_bg: tuple
    card_border: tuple
    row_bg: tuple
    text_main: tuple
    text_sub: tuple
    text_muted: tuple
    accent: tuple

    def css(self) -> str:
        return ";".join(
            f"--{k.replace('_', '-')}:rgb({','.join(map(str, v))})"
            for k, v in asdict(self).items()
            if k != "name"
        )


LIGHT = RenderTheme(
    "light",
    (228, 245, 255),
    (243, 251, 255),
    (170, 210, 235),
    (236, 248, 255),
    (25, 55, 75),
    (80, 120, 140),
    (120, 150, 165),
    (35, 125, 175),
)
DARK = RenderTheme(
    "dark",
    (30, 32, 40),
    (40, 44, 55),
    (60, 65, 80),
    (48, 52, 65),
    (220, 225, 235),
    (160, 170, 185),
    (110, 120, 135),
    (120, 230, 210),
)


def resolve_theme(
    mode: ThemeMode = "auto", *, now: datetime | None = None
) -> RenderTheme:
    if mode == "auto":
        tz = ZoneInfo("Asia/Shanghai")
        local = (
            (now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz))
            if now
            else datetime.now(tz)
        )
        mode = "light" if 6 <= local.hour < 18 else "dark"
    if mode not in ("light", "dark"):
        raise ValueError(f"Unknown theme: {mode}")
    return LIGHT if mode == "light" else DARK
