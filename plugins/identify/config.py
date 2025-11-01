from pathlib import Path

# 插件根目录
PLUGIN_DIR = Path(__file__).parent

# 资源文件目录
RESOURCES_DIR = PLUGIN_DIR / "resources"

# 数据文件目录
DATA_DIR = PLUGIN_DIR / "data"
DAILY_RECORDS_FILE = DATA_DIR / "daily_records.json"

# 确保目录存在
RESOURCES_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)