"""deer_pipe 插件常量定义模块"""

from utils.paths import PluginPaths

from pathlib import Path


# 插件信息
PLUGIN_PATH: Path = Path(__file__).parent.resolve()
PLUGIN_ID: str = "deer_pipe"

# 日历模板与贴图（模板通过相对路径 ../assets 引用贴图和字体）
TEMPLATE_DIR: Path = PLUGIN_PATH / "templates"

# 数据库配置
DATABASE_VERSION: int = 3  # 升级版本号以支持年份字段
DATABASE_NAME: str = f"userdata-v{DATABASE_VERSION}.db"
DATABASE_PATH: Path = PluginPaths("deer_pipe").data / DATABASE_NAME
DATABASE_URL: str = f"sqlite+aiosqlite:///{DATABASE_PATH}"
