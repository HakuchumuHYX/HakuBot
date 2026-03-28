# plugins/buaa_msm/config.py
"""
BUAA MSM 插件统一配置文件（纯运行配置）。

注意：游戏领域常量（角色名、站点ID、地图等）位于 domain/constants.py。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Sequence

from pydantic import BaseModel
from nonebot import require
from nonebot.log import logger

# 声明依赖并导入 nonebot-plugin-localstore
require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store


class CleanupConfig(BaseModel):
    """清理任务配置"""
    morning_hour: int = 5
    morning_minute: int = 0
    afternoon_hour: int = 17
    afternoon_minute: int = 0


class TimeConfig(BaseModel):
    """时间段配置"""
    morning_start: int = 5
    afternoon_start: int = 17


class PluginConfig:
    """插件配置类（纯运行参数，不包含游戏领域常量）"""

    def __init__(self) -> None:
        # 路径配置
        self.plugin_dir: Path = Path(__file__).parent
        self.data_dir: Path = store.get_plugin_data_dir()
        self.file_storage_dir: Path = self.data_dir / "msmdata"
        self.resource_dir: Path = self.plugin_dir / "resources"
        self.visit_history_file: Path = self.data_dir / "visit_history.json"
        self.bind_data_file: Path = self.plugin_dir / "bind.json"
        self.user_latest_files_index_file: Path = self.data_dir / "user_latest_files.json"
        self.local_config_file: Path = self.plugin_dir / "config.json"

        # 本地 JSON 配置（若缺失则回退默认值）
        local_cfg = self._load_local_json_config()

        # 外部数据路径（优先级：环境变量 > config.json > 默认值）
        default_master_data_dir = str(self.plugin_dir / "../../../haruki-sekai-master/master")
        json_master_data_dir = self._as_str(
            self._deep_get(local_cfg, ("master_data_dir",), default_master_data_dir),
            default_master_data_dir,
        )
        env_master_data_dir = os.environ.get("BUAA_MSM_MASTER_DATA_DIR")
        final_master_data_dir = env_master_data_dir or json_master_data_dir
        self.master_data_dir: Path = Path(final_master_data_dir)

        if env_master_data_dir:
            logger.info("BUAA_MSM 配置来源: BUAA_MSM_MASTER_DATA_DIR (env)")
        if not self.master_data_dir.exists():
            logger.warning(
                f"BUAA_MSM master_data_dir 不存在: {self._mask_path(self.master_data_dir)}，"
                "运行时将可能无法读取 master data。"
            )

        # Asset 站配置（用于获取唱片封面等远程资源）
        default_asset_url_base = "https://example.invalid/"
        default_asset_server = "jp"
        self.asset_url_base: str = self._as_str(
            self._deep_get(local_cfg, ("asset", "url_base"), default_asset_url_base),
            default_asset_url_base,
        )
        self.asset_server: str = self._as_str(
            self._deep_get(local_cfg, ("asset", "server"), default_asset_server),
            default_asset_server,
        )

        # MySekai 动态图标配置
        self.mysekai_dynamic_icon_enabled: bool = self._as_bool(
            self._deep_get(local_cfg, ("mysekai_icon", "enabled"), True),
            True,
        )
        self.mysekai_icon_url_base: str = self._as_str(
            self._deep_get(local_cfg, ("mysekai_icon", "url_base"), self.asset_url_base),
            self.asset_url_base,
        )
        self.mysekai_icon_server: str = self._as_str(
            self._deep_get(local_cfg, ("mysekai_icon", "server"), self.asset_server),
            self.asset_server,
        )
        self.mysekai_icon_cache_dir: Path = self.data_dir / "mysekai_icon_cache"
        self.mysekai_icon_download_timeout: float = self._as_float(
            self._deep_get(local_cfg, ("mysekai_icon", "download_timeout"), 5.0),
            5.0,
        )
        self.mysekai_icon_prefetch_concurrency: int = self._as_int(
            self._deep_get(local_cfg, ("mysekai_icon", "prefetch_concurrency"), 8),
            8,
        )
        self.mysekai_icon_download_retries: int = self._as_int(
            self._deep_get(local_cfg, ("mysekai_icon", "download_retries"), 2),
            2,
        )
        self.mysekai_icon_retry_backoff_seconds: float = self._as_float(
            self._deep_get(local_cfg, ("mysekai_icon", "retry_backoff_seconds"), 0.3),
            0.3,
        )
        self.jacket_download_timeout: float = self._as_float(
            self._deep_get(local_cfg, ("jacket", "download_timeout"), 5.0),
            5.0,
        )
        self.jacket_download_retries: int = self._as_int(
            self._deep_get(local_cfg, ("jacket", "download_retries"), 2),
            2,
        )
        self.jacket_download_concurrency: int = self._as_int(
            self._deep_get(local_cfg, ("jacket", "download_concurrency"), 8),
            8,
        )
        self.jacket_retry_backoff_seconds: float = self._as_float(
            self._deep_get(local_cfg, ("jacket", "retry_backoff_seconds"), 0.25),
            0.25,
        )

        # 上传流程配置
        self.upload_wait_timeout_seconds: int = self._as_int(
            self._deep_get(local_cfg, ("upload", "wait_timeout_seconds"), 600),
            600,
        )

        # 字体配置
        self.font_name: str = "font.ttf"
        self.font_path: Path = self.resource_dir / self.font_name

        # 定时清理配置
        self.cleanup = CleanupConfig()

        # 时间段配置
        self.time_config = TimeConfig()

        # 确保目录存在
        self._ensure_directories()

        # 安全日志
        logger.info(
            "BUAA_MSM 配置已初始化: "
            f"asset_origin={self._mask_url_origin(self.asset_url_base)}, "
            f"icon_origin={self._mask_url_origin(self.mysekai_icon_url_base)}, "
            f"master_data_dir={self._mask_path(self.master_data_dir)}"
        )

    def _load_local_json_config(self) -> dict[str, Any]:
        """加载 plugins/buaa_msm/config.json（可选）"""
        if not self.local_config_file.exists():
            logger.info("BUAA_MSM 未发现本地 config.json，使用默认值/环境变量。")
            return {}

        try:
            data = json.loads(self.local_config_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("BUAA_MSM config.json 顶层不是对象，已忽略并回退默认值。")
                return {}
            logger.info("BUAA_MSM 已加载本地 config.json。")
            return data
        except Exception as e:
            logger.warning(f"BUAA_MSM 读取 config.json 失败，已回退默认值: {type(e).__name__}")
            return {}

    @staticmethod
    def _deep_get(data: dict[str, Any], keys: Sequence[str], default: Any = None) -> Any:
        cur: Any = data
        for key in keys:
            if not isinstance(cur, dict):
                return default
            if key not in cur:
                return default
            cur = cur[key]
        return cur

    @staticmethod
    def _as_str(value: Any, default: str) -> str:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip()
            return text if text else default
        return str(value)

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    @staticmethod
    def _mask_url_origin(url: str) -> str:
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(str(url))
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            pass
        return "(invalid-url)"

    @staticmethod
    def _mask_path(path: Path) -> str:
        parts = list(path.parts)
        if len(parts) <= 2:
            return str(path)
        return str(Path(parts[0]) / "..." / parts[-1])

    def _ensure_directories(self) -> None:
        """确保所有必要的目录存在"""
        self.file_storage_dir.mkdir(parents=True, exist_ok=True)
        self.resource_dir.mkdir(parents=True, exist_ok=True)
        self.mysekai_icon_cache_dir.mkdir(parents=True, exist_ok=True)
        (self.resource_dir / "img").mkdir(parents=True, exist_ok=True)
        (self.resource_dir / "icon" / "Texture2D").mkdir(parents=True, exist_ok=True)


# 全局配置实例
plugin_config = PluginConfig()
