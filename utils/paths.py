"""项目内外各类目录的唯一来源；只拼路径，不创建目录"""

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
            raise ValueError(f"插件 ID 不合法: {self.plugin_id}")

    @property
    def config(self):
        return project_root() / "config" / self.plugin_id

    @property
    def data(self):
        return project_root() / "data" / self.plugin_id

    @property
    def cache(self):
        return project_root() / "cache" / self.plugin_id

    @property
    def resources(self):
        return project_root() / "plugins" / self.plugin_id / "resources"


def sekai_master_dir() -> Path:
    """haruki-sekai-master 仓库（PJSK masterdata），和项目根目录并列"""
    return project_root().parent / "haruki-sekai-master"


def moesekai_hub_dir() -> Path:
    """MoeSekai-Hub 稀疏镜像（PJSK 剧情文本），和项目根目录并列"""
    return project_root().parent / "moesekai-hub"
