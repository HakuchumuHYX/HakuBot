"""给 webconsole 改命令等级的接口，挂在 NoneBot 自带的 FastAPI 上

nginx 不转发这组路径，只有本机能访问；但它能改权限，同机其他进程也连得上本机端口，所以仍然要 token。
"""

import secrets

import nonebot
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from core.levels import (
    CommandSpec,
    Level,
    command_overrides,
    commands,
    required_level,
    set_command_level,
)


class LevelBody(BaseModel):
    # 0 等于被拉黑的人也能用，没有意义
    level: int = Field(ge=1, le=3)


def _item(spec: CommandSpec) -> dict:
    return {
        "id": spec.id,
        "plugin": spec.id.split(":", 1)[0],
        "default": int(spec.default),
        "override": command_overrides.get(spec.id),
        "level": int(required_level(spec.id)),
    }


def _registered(command_id: str) -> CommandSpec:
    if command_id not in commands:
        raise HTTPException(404, f"未登记的命令: {command_id}")
    return commands[command_id]


def mount(token: str) -> None:
    expected = f"Bearer {token}".encode()

    async def authorize(authorization: str = Header("")):
        if not secrets.compare_digest(authorization.encode(), expected):
            raise HTTPException(401)

    router = APIRouter(
        prefix="/webconsole_bridge/api/command-levels",
        dependencies=[Depends(authorize)],
    )

    @router.get("")
    async def list_command_levels():
        return [_item(commands[command_id]) for command_id in sorted(commands)]

    @router.put("/{command_id}")
    async def put_command_level(command_id: str, body: LevelBody):
        spec = _registered(command_id)
        set_command_level(command_id, Level(body.level))
        return _item(spec)

    @router.delete("/{command_id}")
    async def delete_command_level(command_id: str):
        spec = _registered(command_id)
        set_command_level(command_id, spec.default)
        return _item(spec)

    nonebot.get_app().include_router(router)
