# plugins/buaa_msm/infra/storage.py
"""
文件存储（infra）

职责：
- 管理用户上传的 .bin 文件与关联的解密 json 文件
- 维护“每个用户最新文件”的内存索引（user_latest_files）
- 将 user_latest_files 持久化到索引文件，降低仅依赖文件名解析的耦合

说明：
- 原实现来自 `plugins/buaa_msm/data_manage.py`，为拆分职责迁移至此。
- 不应包含 NoneBot 命令/定时任务注册（这些应在 handlers）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict

from nonebot.log import logger

from ..config import plugin_config

# 从配置中获取路径
file_storage_dir: Path = plugin_config.file_storage_dir
user_latest_files_index_file: Path = plugin_config.user_latest_files_index_file

# 存储每个QQ号的最新文件路径（内存索引）
user_latest_files: Dict[str, Path] = {}


def extract_user_id_from_filename(filename: str) -> str:
    """从文件名中提取QQ号"""
    match = re.match(r"^(\d+)_.*\.bin$", filename, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.match(r"^(\d+)", filename)
    if match:
        return match.group(1)

    return ""


def _persist_user_latest_files() -> None:
    """将 user_latest_files 持久化到索引文件"""
    try:
        user_latest_files_index_file.parent.mkdir(parents=True, exist_ok=True)
        serializable = {uid: str(path) for uid, path in user_latest_files.items()}
        user_latest_files_index_file.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"持久化 user_latest_files 失败: {e}")


def _load_user_latest_files_index() -> Dict[str, Path]:
    """从索引文件加载 user_latest_files（仅返回存在且合法的 .bin 路径）"""
    if not user_latest_files_index_file.exists():
        return {}

    try:
        raw = json.loads(user_latest_files_index_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}

        loaded: Dict[str, Path] = {}
        for user_id, path_str in raw.items():
            if not isinstance(user_id, str) or not isinstance(path_str, str):
                continue
            p = Path(path_str)
            if p.exists() and p.is_file() and p.suffix.lower() == ".bin":
                loaded[user_id] = p
        return loaded
    except Exception as e:
        logger.error(f"加载 user_latest_files 索引失败: {e}")
        return {}


def _delete_user_file(user_id: str, file_path: Path):
    """删除用户文件及其关联的JSON"""
    try:
        file_stem = file_path.stem
        file_path.unlink()
        logger.info(f"删除用户 {user_id} 的旧文件: {file_path.name}")

        # 删除关联的JSON文件
        user_output_dir = file_storage_dir / f"output_{user_id}"
        json_to_delete = user_output_dir / f"{file_stem}_decrypted.json"
        if json_to_delete.exists():
            json_to_delete.unlink()
            logger.info(f"删除用户 {user_id} 的旧JSON: {json_to_delete.name}")
    except FileNotFoundError:
        logger.warning(f"尝试删除的文件 {file_path.name} 已不存在")
    except Exception as e:
        logger.error(f"删除文件失败: {e}")


def update_user_latest_file(user_id: str, file_path: Path):
    """更新用户的最新文件记录（如更“新”则替换并清理旧文件）"""
    global user_latest_files

    if user_id not in user_latest_files:
        user_latest_files[user_id] = file_path
        _persist_user_latest_files()
        return

    changed = False
    try:
        current_mtime = user_latest_files[user_id].stat().st_mtime
        new_mtime = file_path.stat().st_mtime

        if new_mtime > current_mtime:
            # 删除旧文件
            _delete_user_file(user_id, user_latest_files[user_id])
            user_latest_files[user_id] = file_path
            changed = True
        elif new_mtime < current_mtime:
            # 删除刚上传的旧文件
            try:
                file_path.unlink()
                logger.info(f"删除了刚上传的旧文件: {file_path.name}")
            except Exception as e:
                logger.error(f"删除刚上传的旧文件失败: {e}")
    except FileNotFoundError:
        logger.warning(f"文件 {user_latest_files[user_id].name} 在检查时已不存在")
        user_latest_files[user_id] = file_path
        changed = True

    if changed:
        _persist_user_latest_files()


def remove_old_user_files(user_id: str, keep_file: Path):
    """删除用户除指定文件外的所有其他文件"""
    if not file_storage_dir.exists():
        return

    deleted_count = 0
    for file_path in file_storage_dir.iterdir():
        if file_path.is_file() and file_path.suffix.lower() == ".bin" and file_path != keep_file:
            file_user_id = extract_user_id_from_filename(file_path.name)
            if file_user_id == user_id:
                _delete_user_file(user_id, file_path)
                deleted_count += 1

    if deleted_count > 0:
        logger.info(f"为用户 {user_id} 删除了 {deleted_count} 个旧文件")


def clear_user_latest_files() -> None:
    """清空内存索引并持久化空索引文件"""
    user_latest_files.clear()
    _persist_user_latest_files()


def load_user_latest_files():
    """加载已存在的文件，初始化用户最新文件字典（索引优先，文件扫描兜底）"""
    global user_latest_files
    user_latest_files = {}

    # 1) 优先加载显式索引
    loaded = _load_user_latest_files_index()
    if loaded:
        user_latest_files.update(loaded)

    # 2) 文件扫描兜底（用于首次升级或索引缺失/损坏）
    if file_storage_dir.exists():
        try:
            for file_path in file_storage_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() == ".bin":
                    user_id = extract_user_id_from_filename(file_path.name)
                    if user_id:
                        if user_id not in user_latest_files:
                            user_latest_files[user_id] = file_path
                        else:
                            old = user_latest_files[user_id]
                            try:
                                if file_path.stat().st_mtime > old.stat().st_mtime:
                                    user_latest_files[user_id] = file_path
                            except Exception:
                                pass
        except Exception as e:
            logger.error(f"扫描用户文件记录失败: {e}")

    # 3) 回写规范化后的索引
    _persist_user_latest_files()
    logger.info(f"已加载 {len(user_latest_files)} 个用户的文件记录")
