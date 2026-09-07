"""Read-only configuration loading. Writes require an explicit management action."""

from utils.json_io import load_json, atomic_write_json
from utils.paths import PluginPaths
from copy import deepcopy


def load_config(plugin_id, model, filename="config.json"):
    result = load_json(PluginPaths(plugin_id).config / filename)
    if not result.success:
        raise ValueError(f"Invalid configuration for {plugin_id}: {result.error}")
    return model.model_validate(result.data)


def update_fields(path, changes):
    result = load_json(path)
    if not result.success:
        raise ValueError(f"Refusing to overwrite invalid configuration: {path}")

    def merge(target, patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    merge(result.data, changes)
    atomic_write_json(path, result.data)
