from pathlib import Path

from nonebot import logger

from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths, sekai_master_dir


class AssetConfig(ConfigModel):
    # 资源站地址只放 config.json；动态图标和唱片封面都从这里取
    url_base: str
    server: str


class ApiCryptoConfig(ConfigModel):
    # API 包体解密密钥，不写入代码
    key: str
    iv: str


class Settings(ConfigModel):
    """config/buaa_msm/config.json，结构与文件一一对应"""

    # 网页版地址；配了就附在部分回复末尾
    link: str
    asset: AssetConfig
    api_crypto: ApiCryptoConfig


settings = load_config("buaa_msm", Settings)

RESOURCE_DIR = Path(__file__).parent / "resources"
DATA_DIR = PluginPaths("buaa_msm").data
# 每人一个子目录，放数据包和结果图；每天定时整体清空
STORAGE_DIR = DATA_DIR / "msmdata"
ICON_CACHE_DIR = DATA_DIR / "mysekai_icon_cache"
# 收获点本体贴图，手动放置的静态素材，不进 git
HARVEST_FIXTURE_ICON_DIR = DATA_DIR / "harvest_fixture_icon"
BIND_FILE = DATA_DIR / "bind.json"
VISIT_HISTORY_FILE = DATA_DIR / "visit_history.json"
# 网页登录会话；不放进 msmdata/，定时清理不会把人登出
WEB_SESSIONS_FILE = DATA_DIR / "web_sessions.json"

# 烤森每天这两个整点刷新：上传数据在这两个时间清理，来访记录按这两个时间分时段
REFRESH_HOURS = (5, 17)

if not (sekai_master_dir() / "master").exists():
    logger.warning(
        f"BUAA_MSM 找不到 masterdata 目录: {sekai_master_dir() / 'master'}，"
        "运行时将可能无法读取 master data。"
    )


def with_site_link(text: str) -> str:
    """配了网页版地址时，在回复末尾提示也可以去网页操作"""
    if not settings.link:
        return text
    return f"{text}\n也可以通过网站：{settings.link} 进行操作"
