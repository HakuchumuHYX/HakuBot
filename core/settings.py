"""插件配置读取；改配置只能通过管理命令显式调用 update_fields"""

from utils.json_io import load_json, atomic_write_json
from utils.paths import PluginPaths
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, ValidationError


class ConfigModel(BaseModel):
    """插件配置模型的基类：多出来的键直接报错，拼错的键名和废弃字段在启动时就暴露。"""

    model_config = ConfigDict(extra="forbid")


def load_config(plugin_id, model, filename="config.json"):
    """读取并校验插件配置；缺文件、读不出来、校验不过都抛异常，由 NoneBot 记为该插件加载失败。"""
    result = load_json(PluginPaths(plugin_id).config / filename)
    if not result.success:
        raise ValueError(f"{plugin_id} 的配置文件无法读取: {result.error}")
    try:
        return model.model_validate(result.data)
    except ValidationError as e:
        raise ValueError(f"{plugin_id} 的配置校验不通过: {e}") from e


def update_fields(path, changes):
    result = load_json(path)
    if not result.success:
        raise ValueError(f"配置文件已损坏，拒绝覆盖写入: {path}")

    def merge(target, patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    merge(result.data, changes)
    atomic_write_json(path, result.data)
