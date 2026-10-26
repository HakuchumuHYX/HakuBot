"""alive_stat 配置：config/alive_stat/config.json。"""

from core.settings import ConfigModel, load_config


class ProcessEntry(ConfigModel):
    name: str
    keyword: str


class DockerEntry(ConfigModel):
    name: str
    container: str


class AliveConfig(ConfigModel):
    autochat_data_file: str | None
    monitored_processes: list[ProcessEntry]
    docker_processes: list[DockerEntry]
    ping_hosts: list[str]


config = load_config("alive_stat", AliveConfig)
