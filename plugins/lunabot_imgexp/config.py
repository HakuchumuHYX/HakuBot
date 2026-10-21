from typing import Optional

from core.settings import ConfigModel, load_config


class Config(ConfigModel):
    saucenao_apikey: str
    # 没配就跳过 GoogleLens 搜索
    serp_apikey: str
    proxy: Optional[str]
    watermark_text: str
    # Ascii2D 搜索要过 FlareSolverr，没配就跳过 Ascii2D
    flaresolverr_url: str


config = load_config("lunabot_imgexp", Config)
