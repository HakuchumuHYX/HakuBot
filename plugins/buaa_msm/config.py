# plugins/buaa_msm/config.py
"""
BUAA MSM 插件统一配置文件（纯运行配置）。

注意：游戏领域常量（角色名、站点ID、地图等）位于 domain/constants.py。
"""

from __future__ import annotations
from utils.paths import PluginPaths

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from nonebot import logger

from core.settings import ConfigModel, load_config
from utils.paths import sekai_master_dir


class AssetConfig(ConfigModel):
    # 资源站地址只放 config.json
    url_base: str
    server: str


class MysekaiIconConfig(ConfigModel):
    enabled: bool
    url_base: str
    server: str
    download_timeout: float
    prefetch_concurrency: int = Field(ge=1)
    download_retries: int = Field(ge=0)
    retry_backoff_seconds: float = Field(ge=0)


class JacketConfig(ConfigModel):
    download_timeout: float
    download_retries: int = Field(ge=0)
    download_concurrency: int = Field(ge=1)
    retry_backoff_seconds: float = Field(ge=0)


class UploadConfig(ConfigModel):
    wait_timeout_seconds: int = Field(ge=1)


class ApiCryptoConfig(ConfigModel):
    # API 包体解密密钥，不写入代码
    key: str
    iv: str


class Settings(ConfigModel):
    """config/plugins/buaa_msm/config.json，结构与文件一一对应"""

    # 网页版地址；配了就附在部分回复末尾
    link: str
    asset: AssetConfig
    mysekai_icon: MysekaiIconConfig
    jacket: JacketConfig
    upload: UploadConfig
    api_crypto: ApiCryptoConfig


settings = load_config("buaa_msm", Settings)


class CleanupConfig(BaseModel):
    """清理任务配置"""

    morning_hour: int = 5
    morning_minute: int = 0
    afternoon_hour: int = 17
    afternoon_minute: int = 0


class TimeConfig(BaseModel):
    """时间段配置"""

    morning_start: int = 5
    afternoon_start: int = 17


class PluginConfig:
    """插件配置类（纯运行参数，不包含游戏领域常量）"""

    def __init__(self) -> None:
        # 路径配置
        self.plugin_dir: Path = Path(__file__).parent
        self.data_dir: Path = PluginPaths("buaa_msm").data
        self.file_storage_dir: Path = self.data_dir / "msmdata"
        self.resource_dir: Path = self.plugin_dir / "resources"
        self.visit_history_file: Path = self.data_dir / "visit_history.json"
        self.bind_data_file: Path = self.plugin_dir / "bind.json"
        self.user_latest_files_index_file: Path = (
            self.data_dir / "user_latest_files.json"
        )
        # 网页登录会话；不放进 msmdata/，定时清理不会把人登出
        self.web_sessions_file: Path = self.data_dir / "web_sessions.json"
        self.mysekai_icon_cache_dir: Path = self.data_dir / "mysekai_icon_cache"

        # 字体配置
        self.font_name: str = "font.ttf"
        self.font_path: Path = self.resource_dir / self.font_name

        # 定时清理配置
        self.cleanup = CleanupConfig()

        # 时间段配置
        self.time_config = TimeConfig()

        # 确保目录存在
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """确保所有必要的目录存在"""
        self.file_storage_dir.mkdir(parents=True, exist_ok=True)
        self.resource_dir.mkdir(parents=True, exist_ok=True)
        self.mysekai_icon_cache_dir.mkdir(parents=True, exist_ok=True)
        (self.resource_dir / "img").mkdir(parents=True, exist_ok=True)
        (self.resource_dir / "icon" / "Texture2D").mkdir(parents=True, exist_ok=True)


def _mask_url_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "(invalid-url)"


# 全局配置实例
plugin_config = PluginConfig()

if not (sekai_master_dir() / "master").exists():
    logger.warning(
        f"BUAA_MSM 找不到 masterdata 目录: {sekai_master_dir() / 'master'}，"
        "运行时将可能无法读取 master data。"
    )
logger.info(
    "BUAA_MSM 配置已初始化: "
    f"asset_origin={_mask_url_origin(settings.asset.url_base)}, "
    f"icon_origin={_mask_url_origin(settings.mysekai_icon.url_base)}"
)


def with_site_link(text: str) -> str:
    """配了网页版地址时，在回复末尾提示也可以去网页操作"""
    if not settings.link:
        return text
    return f"{text}\n也可以通过网站：{settings.link} 进行操作"
