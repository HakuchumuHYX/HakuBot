"""
alive_stat 配置加载模块。
从 config.json 读取敏感配置，如果文件不存在则使用空默认值。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PLUGIN_DIR = Path(__file__).parent
CONFIG_FILE = PLUGIN_DIR / "config.json"


@dataclass
class ProcessEntry:
    name: str
    keyword: str


@dataclass
class DockerEntry:
    name: str
    container: str


@dataclass
class AliveConfig:
    autochat_data_file: Optional[str] = None
    monitored_processes: list[ProcessEntry] = field(default_factory=list)
    docker_processes: list[DockerEntry] = field(default_factory=list)
    ping_hosts: list[str] = field(default_factory=lambda: ["baidu.com", "google.com"])


def load_config() -> AliveConfig:
    """加载 config.json，不存在则返回默认空配置。"""
    if not CONFIG_FILE.exists():
        return AliveConfig()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return AliveConfig(
            autochat_data_file=raw.get("autochat_data_file"),
            monitored_processes=[
                ProcessEntry(name=p["name"], keyword=p["keyword"])
                for p in raw.get("monitored_processes", [])
            ],
            docker_processes=[
                DockerEntry(name=d["name"], container=d["container"])
                for d in raw.get("docker_processes", [])
            ],
            ping_hosts=raw.get("ping_hosts", ["baidu.com", "google.com"]),
        )
    except Exception:
        return AliveConfig()


config = load_config()
