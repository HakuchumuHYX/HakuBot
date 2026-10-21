"""alive_stat 配置：config/plugins/alive_stat/config.json。"""

from typing import Optional

from core.settings import ConfigModel, load_config


class ProcessEntry(ConfigModel):
    name: str
    keyword: str


class DockerEntry(ConfigModel):
    name: str
    container: str


class AliveConfig(ConfigModel):
    autochat_data_file: Optional[str]
    monitored_processes: list[ProcessEntry]
    docker_processes: list[DockerEntry]
    ping_hosts: list[str]


config = load_config("alive_stat", AliveConfig)
