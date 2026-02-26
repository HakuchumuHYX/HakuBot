"""deer_pipe 插件数据库操作模块"""

from base64 import b64decode, b64encode
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, Sequence
from uuid import UUID, uuid4

from nonebot import logger
from nonebot_plugin_apscheduler import scheduler
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlmodel import Field, SQLModel, delete, select, update

from .constants import DATABASE_URL


# ORM 模型
class User(SQLModel, table=True):
    """用户信息表"""
    user_id: str = Field(primary_key=True)
    avatar: str | None = None


class UserDeer(SQLModel, table=True):
    """用户签到记录表"""
    uuid: UUID = Field(primary_key=True, default_factory=uuid4)
    user_id: str = Field(index=True)
    year: int  # 新增年份字段
    month: int
    day: int
    count: int = 1


# 数据库引擎（禁用 echo 以使用 NoneBot 日志）
engine: AsyncEngine = create_async_engine(DATABASE_URL, echo=False)
_initialized: bool = False


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话的上下文管理器"""
    global _initialized
    
    # 首次使用时初始化数据库
    if not _initialized:
        logger.info("初始化 deer_pipe 数据库...")
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        _initialized = True
        logger.success("deer_pipe 数据库初始化完成")
    
    # 创建并返回会话
    async with AsyncSession(engine) as session:
        yield session


@scheduler.scheduled_job(
    "cron",
    day=1,  # 每月1号执行
    hour=4,
    id="deer_pipe_cleanup"
)
async def cleanup() -> None:
    """清理过期的签到数据（保留当前月份的数据）"""
    now = datetime.now()
    logger.info(f"开始清理 deer_pipe 过期数据，当前时间: {now.year}-{now.month}")
    
    try:
        async with get_session() as session:
            # 获取即将被删除的用户ID
            result: Sequence[Row[tuple[str]]] = (
                await session.execute(
                    select(UserDeer.user_id)
                    .distinct()
                    .where(
                        (UserDeer.year != now.year) | (UserDeer.month != now.month)
                    )
                )
            ).all()
            users_to_check: set[str] = {row[0] for row in result}
            
            # 删除非当前月份的签到记录
            delete_result = await session.execute(
                delete(UserDeer).where(
                    (UserDeer.year != now.year) | (UserDeer.month != now.month)
                )
            )
            deleted_count = delete_result.rowcount
            logger.info(f"删除了 {deleted_count} 条过期签到记录")
            
            # 检查哪些用户仍有签到记录
            result = (
                await session.execute(select(UserDeer.user_id).distinct())
            ).all()
            users_with_records: set[str] = {row[0] for row in result}
            
            # 删除没有任何签到记录的用户头像数据
            orphaned_users = users_to_check - users_with_records
            if orphaned_users:
                for user_id in orphaned_users:
                    await session.execute(
                        delete(User).where(User.user_id == user_id)
                    )
                logger.info(f"清理了 {len(orphaned_users)} 个无效用户头像数据")
            
            await session.commit()
            logger.success("deer_pipe 数据清理完成")
            
    except Exception as e:
        logger.error(f"deer_pipe 数据清理失败: {e}")


async def get_avatar(user_id: str) -> bytes | None:
    """获取用户头像"""
    try:
        async with get_session() as session:
            result: Row[tuple[str | None]] | None = (
                await session.execute(
                    select(User.avatar).where(User.user_id == user_id)
                )
            ).first()
            
            if result is None or result[0] is None:
                return None
            
            return b64decode(result[0])
    except Exception as e:
        logger.warning(f"获取用户头像失败 (user_id={user_id}): {e}")
        return None


async def update_avatar(user_id: str, avatar: bytes | None) -> None:
    """更新用户头像"""
    if avatar is None:
        return
    
    try:
        async with get_session() as session:
            result: Row[tuple[User]] | None = (
                await session.execute(
                    select(User).where(User.user_id == user_id)
                )
            ).first()
            
            avatar_b64 = b64encode(avatar).decode()
            
            if result is None:
                session.add(User(user_id=user_id, avatar=avatar_b64))
            else:
                result[0].avatar = avatar_b64
                session.add(result[0])
            
            await session.commit()
            logger.debug(f"更新用户头像成功 (user_id={user_id})")
    except Exception as e:
        logger.warning(f"更新用户头像失败 (user_id={user_id}): {e}")


async def get_deer_map(user_id: str, now: datetime) -> dict[int, int]:
    """获取用户当月签到记录"""
    try:
        async with get_session() as session:
            return await _get_deer_map(session, user_id, now)
    except Exception as e:
        logger.error(f"获取签到记录失败 (user_id={user_id}): {e}")
        return {}


async def _get_deer_map(
    session: AsyncSession, user_id: str, now: datetime
) -> dict[int, int]:
    """内部方法：获取用户当月签到记录"""
    result: Sequence[Row[tuple[int, int]]] = (
        await session.execute(
            select(UserDeer.day, UserDeer.count).where(
                UserDeer.user_id == user_id,
                UserDeer.year == now.year,
                UserDeer.month == now.month,
            )
        )
    ).all()
    
    return {row[0]: row[1] for row in result}


async def attend(user_id: str, now: datetime) -> dict[int, int]:
    """执行签到操作"""
    try:
        async with get_session() as session:
            # 获取当前签到记录
            deer_map = await _get_deer_map(session, user_id, now)
            
            # 更新签到次数
            if now.day in deer_map:
                deer_map[now.day] += 1
                await session.execute(
                    update(UserDeer)
                    .where(
                        UserDeer.user_id == user_id,
                        UserDeer.year == now.year,
                        UserDeer.month == now.month,
                        UserDeer.day == now.day,
                    )
                    .values(count=deer_map[now.day])
                )
                logger.debug(f"用户 {user_id} 重复签到，当日次数: {deer_map[now.day]}")
            else:
                deer_map[now.day] = 1
                session.add(
                    UserDeer(
                        user_id=user_id,
                        year=now.year,
                        month=now.month,
                        day=now.day,
                    )
                )
                logger.debug(f"用户 {user_id} 首次签到")
            
            await session.commit()
            return deer_map
            
    except Exception as e:
        logger.error(f"签到失败 (user_id={user_id}): {e}")
        return {}


async def attend_past(
    user_id: str, now: datetime, day: int
) -> tuple[bool, dict[int, int]]:
    """执行补签操作"""
    try:
        async with get_session() as session:
            # 获取当前签到记录
            deer_map = await _get_deer_map(session, user_id, now)
            
            # 检查是否已签到
            if day in deer_map:
                logger.debug(f"用户 {user_id} 尝试补签已签到的日期: {day}")
                return (False, deer_map)
            
            # 添加补签记录
            deer_map[day] = 1
            session.add(
                UserDeer(
                    user_id=user_id,
                    year=now.year,
                    month=now.month,
                    day=day,
                )
            )
            await session.commit()
            logger.debug(f"用户 {user_id} 补签成功: {now.month}月{day}日")
            
            return (True, deer_map)
            
    except Exception as e:
        logger.error(f"补签失败 (user_id={user_id}, day={day}): {e}")
        return (False, {})
