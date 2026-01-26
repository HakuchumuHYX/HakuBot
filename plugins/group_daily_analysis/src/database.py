import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from nonebot.log import logger

# 数据目录
DATA_DIR = Path("data/group_daily_analysis")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "messages.db"

class DatabaseManager:
    def __init__(self):
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(DB_PATH)

    def _init_db(self):
        """初始化数据库表"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        sender_name TEXT,
                        content TEXT,
                        timestamp INTEGER NOT NULL,
                        message_type TEXT,
                        raw_message TEXT
                    )
                """)
                # 创建索引加速查询
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_time ON messages (group_id, timestamp)")
                conn.commit()
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")

    def add_message(self, group_id: str, user_id: str, sender_name: str, content: str, timestamp: int, msg_type: str = "text", raw_message: str = ""):
        """添加消息"""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO messages (group_id, user_id, sender_name, content, timestamp, message_type, raw_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (str(group_id), str(user_id), sender_name, content, timestamp, msg_type, raw_message))
                conn.commit()
        except Exception as e:
            logger.error(f"写入消息失败: {e}")

    def get_messages(self, group_id: str, start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
        """获取指定时间段的消息"""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM messages 
                    WHERE group_id = ? AND timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp ASC
                """, (str(group_id), start_ts, end_ts))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"查询消息失败: {e}")
            return []

    def cleanup_old_messages(self, retention_days: int = 7):
        """清理旧消息"""
        try:
            cutoff_ts = int(time.time()) - (retention_days * 24 * 3600)
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff_ts,))
                deleted_count = cursor.rowcount
                conn.commit()
                if deleted_count > 0:
                    logger.info(f"已清理 {deleted_count} 条过期消息 (保留 {retention_days} 天)")
                    # 整理碎片
                    cursor.execute("VACUUM")
        except Exception as e:
            logger.error(f"清理旧消息失败: {e}")

db = DatabaseManager()
