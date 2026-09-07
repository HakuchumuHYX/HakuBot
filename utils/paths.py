"""Canonical paths; construction never creates directories."""

from dataclasses import dataclass
import os
from pathlib import Path


def project_root() -> Path:
    return Path(
        os.environ.get("HAKUBOT_ROOT", Path(__file__).resolve().parents[1])
    ).resolve()


@dataclass(frozen=True)
class PluginPaths:
    plugin_id: str

    def __post_init__(self):
        if not self.plugin_id.isidentifier():
            raise ValueError("Invalid plugin ID")

    @property
    def config(self):
        return project_root() / "config/plugins" / self.plugin_id

    @property
    def data(self):
        if self.plugin_id == "identify":
            return project_root() / "plugins/identify/data"
        directory = {
            "groupmate_waifu": "waifu",
            "alive_stat": "alive_stats",
            "sk_predict": "sekai_cache",
        }.get(self.plugin_id, self.plugin_id)
        return project_root() / "data" / directory

    @property
    def cache(self):
        return project_root() / "cache" / self.plugin_id

    @property
    def resources(self):
        return project_root() / "plugins" / self.plugin_id / "resources"


def shared_cache(name: str) -> Path:
    if not name.isidentifier():
        raise ValueError("Invalid cache namespace")
    return project_root() / "cache/shared" / name
