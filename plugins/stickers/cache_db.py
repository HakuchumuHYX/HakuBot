# stickers/cache_db.py
"""
Stickers 插件 - SQLite 缓存数据库管理
使用 SQLite 替代 JSON 文件存储 dHash 缓存，提供更好的性能和可扩展性
"""
import sqlite3
import time
import threading
from pathlib import Path
from typing import Optional, Dict
from contextlib import contextmanager

from nonebot.log import logger

from .send import sticker_dir
from .config import CACHE_VERSION, CACHE_TTL


# 数据库文件路径
DB_FILE = sticker_dir / "hash_cache.db"

# 线程本地存储，每个线程一个连接
_local = threading.local()

# 全局锁，用于初始化
_init_lock = threading.Lock()
_initialized = False


def _get_connection() -> sqlite3.Connection:
    """获取当前线程的数据库连接（线程安全）"""
    if not hasattr(_local, 'connection') or _local.connection is None:
        _local.connection = sqlite3.connect(
            str(DB_FILE),
            timeout=30.0,
            check_same_thread=False
        )
        _local.connection.row_factory = sqlite3.Row
        # 开启 WAL 模式，提高并发性能
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA synchronous=NORMAL")
    return _local.connection


@contextmanager
def get_db():
    """获取数据库连接的上下文管理器"""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e


def init_database():
    """初始化数据库表结构"""
    global _initialized
    
    if _initialized:
        return
    
    with _init_lock:
        if _initialized:
            return
        
        logger.info(f"初始化哈希缓存数据库: {DB_FILE}")
        
        with get_db() as conn:
            # 创建主表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hash_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_mtime REAL NOT NULL,
                    dhash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(file_path, file_size, file_mtime)
                )
            """)
            
            # 创建元数据表（存储版本信息）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            
            # 创建索引
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_path 
                ON hash_cache(file_path)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_time 
                ON hash_cache(created_at)
            """)
            
            # 检查版本
            cursor = conn.execute(
                "SELECT value FROM cache_meta WHERE key = 'version'"
            )
            row = cursor.fetchone()
            
            if row is None:
                # 新数据库，写入版本
                conn.execute(
                    "INSERT INTO cache_meta (key, value) VALUES ('version', ?)",
                    (CACHE_VERSION,)
                )
                logger.info(f"新建缓存数据库，版本: {CACHE_VERSION}")
            elif row['value'] != CACHE_VERSION:
                # 版本不匹配，清空缓存
                old_version = row['value']
                logger.warning(
                    f"缓存版本不匹配 (当前: {old_version}, 需要: {CACHE_VERSION})，清空缓存"
                )
                conn.execute("DELETE FROM hash_cache")
                conn.execute(
                    "UPDATE cache_meta SET value = ? WHERE key = 'version'",
                    (CACHE_VERSION,)
                )
        
        _initialized = True
        logger.info("哈希缓存数据库初始化完成")


def _get_file_key(file_path: Path) -> tuple:
    """获取文件的缓存键（路径、大小、修改时间）"""
    try:
        stat = file_path.stat()
        return (str(file_path.absolute()), stat.st_size, stat.st_mtime)
    except OSError:
        return None


def get_cached_hash(file_path: Path) -> Optional[str]:
    """
    从数据库获取缓存的 dHash
    
    Args:
        file_path: 图片文件路径
        
    Returns:
        缓存的 dHash，或 None（未找到/已过期）
    """
    init_database()
    
    if not file_path.exists():
        return None
    
    key = _get_file_key(file_path)
    if key is None:
        return None
    
    file_path_str, file_size, file_mtime = key
    current_time = time.time()
    
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT dhash, created_at FROM hash_cache
            WHERE file_path = ? AND file_size = ? AND file_mtime = ?
        """, (file_path_str, file_size, file_mtime))
        
        row = cursor.fetchone()
        
        if row is None:
            return None
        
        # 检查是否过期
        if current_time - row['created_at'] > CACHE_TTL:
            # 过期，删除记录
            conn.execute("""
                DELETE FROM hash_cache
                WHERE file_path = ? AND file_size = ? AND file_mtime = ?
            """, (file_path_str, file_size, file_mtime))
            return None
        
        return row['dhash']


def update_cache(file_path: Path, dhash: str):
    """
    更新缓存
    
    Args:
        file_path: 图片文件路径
        dhash: 计算得到的 dHash
    """
    init_database()
    
    if not file_path.exists():
        return
    
    key = _get_file_key(file_path)
    if key is None:
        return
    
    file_path_str, file_size, file_mtime = key
    current_time = time.time()
    
    with get_db() as conn:
        # 使用 REPLACE 语句，存在则更新，不存在则插入
        conn.execute("""
            INSERT OR REPLACE INTO hash_cache 
            (file_path, file_size, file_mtime, dhash, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (file_path_str, file_size, file_mtime, dhash, current_time))


def invalidate_cache(file_path: Path):
    """
    使指定文件的缓存失效
    
    Args:
        file_path: 图片文件路径
    """
    init_database()
    
    file_path_str = str(file_path.absolute())
    
    with get_db() as conn:
        conn.execute(
            "DELETE FROM hash_cache WHERE file_path = ?",
            (file_path_str,)
        )


def clear_all_cache():
    """清空所有缓存"""
    init_database()
    
    with get_db() as conn:
        conn.execute("DELETE FROM hash_cache")
    
    logger.info("哈希缓存已清空")


def cleanup_expired():
    """清理过期的缓存条目"""
    init_database()
    
    current_time = time.time()
    expire_before = current_time - CACHE_TTL
    
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM hash_cache WHERE created_at < ?",
            (expire_before,)
        )
        deleted_count = cursor.rowcount
    
    if deleted_count > 0:
        logger.info(f"清理了 {deleted_count} 条过期缓存")
    
    return deleted_count


def cleanup_orphaned():
    """清理孤儿缓存（文件已不存在的缓存）"""
    init_database()
    
    deleted_count = 0
    
    with get_db() as conn:
        cursor = conn.execute("SELECT id, file_path FROM hash_cache")
        rows = cursor.fetchall()
        
        ids_to_delete = []
        for row in rows:
            if not Path(row['file_path']).exists():
                ids_to_delete.append(row['id'])
        
        if ids_to_delete:
            placeholders = ','.join('?' * len(ids_to_delete))
            conn.execute(
                f"DELETE FROM hash_cache WHERE id IN ({placeholders})",
                ids_to_delete
            )
            deleted_count = len(ids_to_delete)
    
    if deleted_count > 0:
        logger.info(f"清理了 {deleted_count} 条孤儿缓存（文件已不存在）")
    
    return deleted_count


def get_cache_stats() -> Dict:
    """
    获取缓存统计信息
    
    Returns:
        包含统计信息的字典
    """
    init_database()
    
    with get_db() as conn:
        # 总条目数
        cursor = conn.execute("SELECT COUNT(*) as count FROM hash_cache")
        total_count = cursor.fetchone()['count']
        
        # 数据库文件大小
        db_size_mb = DB_FILE.stat().st_size / 1024 / 1024 if DB_FILE.exists() else 0
        
        # 版本
        cursor = conn.execute(
            "SELECT value FROM cache_meta WHERE key = 'version'"
        )
        row = cursor.fetchone()
        version = row['value'] if row else 'unknown'
    
    return {
        "version": version,
        "entries_count": total_count,
        "db_size_mb": round(db_size_mb, 2),
        "db_path": str(DB_FILE)
    }


def vacuum_database():
    """压缩数据库文件（回收空间）"""
    init_database()
    
    with get_db() as conn:
        conn.execute("VACUUM")
    
    logger.info("数据库已压缩")


# ==================== 迁移功能 ====================

def migrate_from_json():
    """
    从旧的 JSON 缓存迁移到 SQLite
    
    Returns:
        迁移的条目数，或 -1 表示无需迁移
    """
    import json
    
    json_cache_file = sticker_dir / "hash_cache.json"
    
    if not json_cache_file.exists():
        logger.info("未发现旧的 JSON 缓存文件，无需迁移")
        return -1
    
    try:
        with open(json_cache_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        entries = json_data.get("entries", {})
        if not entries:
            logger.info("JSON 缓存为空，无需迁移")
            return 0
        
        init_database()
        
        migrated_count = 0
        current_time = time.time()
        
        with get_db() as conn:
            for cache_key, entry in entries.items():
                try:
                    # 解析缓存键：file_path:file_size:file_mtime
                    parts = cache_key.rsplit(':', 2)
                    if len(parts) != 3:
                        continue
                    
                    file_path_str, file_size_str, file_mtime_str = parts
                    file_size = int(file_size_str)
                    file_mtime = float(file_mtime_str)
                    
                    dhash = entry.get("dhash", "")
                    created_at = entry.get("timestamp", current_time)
                    
                    if not dhash:
                        continue
                    
                    conn.execute("""
                        INSERT OR IGNORE INTO hash_cache
                        (file_path, file_size, file_mtime, dhash, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (file_path_str, file_size, file_mtime, dhash, created_at))
                    
                    migrated_count += 1
                    
                except (ValueError, KeyError) as e:
                    logger.warning(f"迁移条目失败: {cache_key}, 错误: {e}")
                    continue
        
        # 备份并删除旧的 JSON 文件
        backup_path = json_cache_file.with_suffix('.json.bak')
        json_cache_file.rename(backup_path)
        
        logger.info(f"从 JSON 迁移了 {migrated_count} 条缓存记录")
        logger.info(f"旧的 JSON 文件已备份到: {backup_path}")
        
        return migrated_count
    
    except Exception as e:
        logger.error(f"迁移 JSON 缓存失败: {e}")
        return -1
