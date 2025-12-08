import json
from pathlib import Path
from nonebot.log import logger

# 数据存储路径
DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "scbind.json"

# 初始化检查
if not DATA_DIR.exists():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_data() -> dict:
    """读取所有绑定数据"""
    if not DATA_FILE.exists():
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取 scbind.json 失败: {e}")
        return {}


def save_sc_bind(qq: str, sc_id: str):
    """保存或更新单个绑定"""
    data = load_data()
    data[qq] = sc_id
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"写入 scbind.json 失败: {e}")


def get_sc_bind(qq: str) -> str | None:
    """(预留功能) 获取某个QQ绑定的ID"""
    data = load_data()
    return data.get(qq)