import json
from pathlib import Path
from typing import Dict, Optional
import nonebot_plugin_localstore as store
from nonebot.log import logger

DATA_FILE = "pjsk_bindings.json"


def get_data_file() -> Path:
    """获取数据文件路径"""
    return store.get_plugin_data_file(DATA_FILE)


def load_data() -> Dict[str, Dict[str, str]]:
    """
    加载绑定数据
    数据结构: { "user_id": { "server": "pjsk_id" } }
    """
    file_path = get_data_file()
    if not file_path.exists():
        return {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载 PJSK 绑定数据失败: {e}")
        return {}


def save_data(data: Dict[str, Dict[str, str]]):
    """保存绑定数据"""
    file_path = get_data_file()
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存 PJSK 绑定数据失败: {e}")


def update_binding(user_id: str, server: str, pjsk_id: str) -> bool:
    """
    更新或添加绑定
    :param user_id: QQ号/用户ID
    :param server: 服务器 (cn/jp/en/tw/kr)
    :param pjsk_id: 游戏内ID
    """
    data = load_data()

    if user_id not in data:
        data[user_id] = {}

    data[user_id][server] = pjsk_id
    save_data(data)
    return True


def get_binding(user_id: str, server: str = "jp") -> Optional[str]:
    """获取指定服务器的绑定ID"""
    data = load_data()
    return data.get(user_id, {}).get(server)