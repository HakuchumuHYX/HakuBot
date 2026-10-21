from typing import Optional

from core.settings import ConfigModel, load_config


class Config(ConfigModel):
    refresh_token: str
    # Pixiv 官方 Android 客户端的公开标识，各第三方客户端通用，不是个人凭据
    client_id: str
    client_secret: str
    proxy: Optional[str]
    timeout: float
    allow_r18: bool
    allow_r18g: bool
    send_original: bool
    max_pages: int
    max_bytes: int
    ugoira_zip_max_bytes: int
    ugoira_max_frames: int
    concurrency: int
    reverse_proxy_domain: Optional[str]


config = load_config("pixiv_id_fetcher", Config)
