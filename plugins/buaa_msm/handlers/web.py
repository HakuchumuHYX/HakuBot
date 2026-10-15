# plugins/buaa_msm/handlers/web.py
"""
网页版接口，挂在 NoneBot 自带的 FastAPI 上。

- nginx 只把 /api/* 转发到这里的 /buaa_msm/api/*，NoneBot 端口上的其他路径不对外
- 登录：ATRI 私聊发验证码；验证码和限流只放内存，重启后作废无所谓
- 上传：请求体就是 .bin 原文，流程与 QQ 私聊上传共用 ingest_bin + render_msr，网页上传不私聊推送结果
- 响应一律不缓存（Cache-Control 由 nginx 统一加）：所有人的结果图 URL 都一样，被 Cloudflare 缓存就会串号
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time

import nonebot
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from nonebot import logger
from pydantic import BaseModel

from plugins.buaa_msm.handlers.bind import bind_manager
from plugins.buaa_msm.infra import storage
from plugins.buaa_msm.infra.web_sessions import SESSION_TTL_SECONDS, web_sessions
from plugins.buaa_msm.services.ingest_service import (
    RESULT_FILES,
    ingest_bin,
    reserve_storage_path,
    user_output_dir,
)
from plugins.buaa_msm.services.msr_service import render_msr
from plugins.buaa_msm.services.processing_guard import is_processing, set_processing
from plugins.buaa_msm.services.user_data_service import get_user_context
from utils.logging import get_exc_desc

COOKIE_NAME = "msm_session"
CODE_TTL_SECONDS = 300
CODE_MAX_ATTEMPTS = 5
QQ_SEND_INTERVAL_SECONDS = 60
IP_SEND_WINDOW_SECONDS = 600
IP_SEND_LIMIT = 5
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
BIND_MAX_CHARS = 40

QQ_RE = re.compile(r"^[1-9]\d{4,11}$")

# {qq: {"code": "123456", "expires": epoch, "attempts": 0}}
_codes: dict[str, dict] = {}
# {qq: 上次发码时间}
_qq_last_sent: dict[str, float] = {}
# {ip: [发码时间, ...]}
_ip_sends: dict[str, list[float]] = {}

router = APIRouter(prefix="/buaa_msm/api")


class CodeRequest(BaseModel):
    qq: str


class LoginRequest(BaseModel):
    qq: str
    code: str


class BindRequest(BaseModel):
    content: str


def _check_qq(qq: str) -> str:
    qq = qq.strip()
    if not QQ_RE.match(qq):
        raise HTTPException(400, "QQ 号格式不对")
    return qq


def _client_ip(request: Request) -> str:
    # nginx 把 CF-Connecting-IP 放进 X-Real-IP；本机直连调试时没有这个头
    return request.headers.get("x-real-ip") or request.client.host


def _current_qq(msm_session: str | None = Cookie(default=None)) -> str:
    qq = web_sessions.get_qq(msm_session) if msm_session else None
    if qq is None:
        raise HTTPException(401, "请先登录")
    return qq


def _me(qq: str) -> dict:
    latest = storage.user_latest_files.get(qq)
    uploaded_at = latest.stat().st_mtime if latest and latest.exists() else None
    out_dir = user_output_dir(qq)
    result = {}
    for kind, name in RESULT_FILES.items():
        path = out_dir / name
        # 图的版本号：前端拼进 ?v=，换了图就换地址
        result[kind] = path.stat().st_mtime_ns if path.exists() else None
    return {
        "qq": qq,
        "bind": bind_manager.get(qq),
        "uploaded_at": uploaded_at,
        **result,
    }


# ============== 登录 ==============


@router.post("/login/code")
async def send_login_code(body: CodeRequest, request: Request):
    qq = _check_qq(body.qq)
    now = time.time()

    wait = QQ_SEND_INTERVAL_SECONDS - (now - _qq_last_sent.get(qq, 0))
    if wait > 0:
        raise HTTPException(429, f"验证码刚发过，{int(wait) + 1} 秒后再试")
    ip = _client_ip(request)
    recent = [t for t in _ip_sends.get(ip, []) if now - t < IP_SEND_WINDOW_SECONDS]
    if len(recent) >= IP_SEND_LIMIT:
        raise HTTPException(429, "发送太频繁了，过几分钟再试")

    try:
        bot = nonebot.get_bot()
    except ValueError:
        raise HTTPException(503, "ATRI 现在不在线，过会儿再试")

    # 先查好友列表：不给陌生人发私聊，也能给出明确的提示
    try:
        friends = await bot.call_api("get_friend_list")
    except Exception as e:
        logger.warning(f"网页登录：获取好友列表失败: {get_exc_desc(e)}")
        raise HTTPException(503, "ATRI 暂时联系不上，过会儿再试")
    if not any(str(f.get("user_id")) == qq for f in friends):
        raise HTTPException(400, "请先加 ATRI 好友（验证信息填群号）")

    code = f"{secrets.randbelow(10**6):06d}"
    try:
        await bot.send_private_msg(
            user_id=int(qq),
            message=f"BUAAMSM 网页登录验证码：{code}，5 分钟内有效。不是你本人操作的话忽略即可。",
        )
    except Exception as e:
        logger.warning(f"网页登录：验证码私聊发送失败 {qq}: {get_exc_desc(e)}")
        raise HTTPException(502, "验证码没发出去，过会儿再试")

    _codes[qq] = {"code": code, "expires": now + CODE_TTL_SECONDS, "attempts": 0}
    _qq_last_sent[qq] = now
    _ip_sends[ip] = recent + [now]
    logger.info(f"网页登录：已向 {qq} 发送验证码")
    return {"ok": True}


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    qq = _check_qq(body.qq)
    entry = _codes.get(qq)
    if entry is None or time.time() > entry["expires"]:
        _codes.pop(qq, None)
        raise HTTPException(400, "验证码已过期，请重新获取")
    if not secrets.compare_digest(body.code.strip(), entry["code"]):
        entry["attempts"] += 1
        if entry["attempts"] >= CODE_MAX_ATTEMPTS:
            _codes.pop(qq, None)
            raise HTTPException(400, "验证码错误次数太多，请重新获取")
        raise HTTPException(400, "验证码不对")

    _codes.pop(qq, None)
    token = web_sessions.create(qq)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    logger.info(f"网页登录：{qq} 登录成功")
    return _me(qq)


@router.post("/logout")
async def logout(response: Response, msm_session: str | None = Cookie(default=None)):
    if msm_session:
        web_sessions.delete(msm_session)
    response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="lax")
    return {"ok": True}


# ============== 数据 ==============


@router.get("/me")
async def me(qq: str = Depends(_current_qq)):
    return _me(qq)


@router.put("/bind")
async def set_bind(body: BindRequest, qq: str = Depends(_current_qq)):
    content = body.content.strip()
    if not content or len(content) > BIND_MAX_CHARS:
        raise HTTPException(400, f"绑定名要在 1 到 {BIND_MAX_CHARS} 个字之间")
    if not bind_manager.set(qq, content):
        raise HTTPException(500, "保存失败，请稍后重试")
    return _me(qq)


@router.delete("/bind")
async def delete_bind(qq: str = Depends(_current_qq)):
    if bind_manager.get(qq) is not None and bind_manager.delete(qq) is None:
        raise HTTPException(500, "保存失败，请稍后重试")
    return _me(qq)


@router.post("/upload")
async def upload(request: Request, name: str, qq: str = Depends(_current_qq)):
    if not name.lower().endswith(".bin"):
        raise HTTPException(400, "请上传 .bin 文件")
    if int(request.headers.get("content-length") or 0) > UPLOAD_MAX_BYTES:
        raise HTTPException(413, "文件太大了")
    if await is_processing(qq):
        raise HTTPException(409, "上一份还在处理，等它完成再传")

    await set_processing(qq, True)
    try:
        body = await request.body()
        if not body:
            raise HTTPException(400, "文件是空的")

        saved_path = reserve_storage_path(name, qq)
        await asyncio.to_thread(saved_path.write_bytes, body)
        logger.info(f"网页上传：用户 {qq} 上传文件 {saved_path.name}（{len(body)} 字节）")

        if not await ingest_bin(qq, saved_path):
            raise HTTPException(400, "解密失败，请确认上传的是 MySekai 数据包")

        ctx_res = await get_user_context(qq)
        if not ctx_res.ok:
            raise HTTPException(400, ctx_res.error)
        summary, map_image = await render_msr(ctx_res.ctx)
        if summary is None and map_image is None:
            raise HTTPException(500, "统计图和位置图都没生成出来，请稍后重试")
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"网页上传：用户 {qq} 的数据处理失败")
        raise HTTPException(500, "生成失败，请稍后重试")
    finally:
        await set_processing(qq, False)

    logger.info(f"网页上传：用户 {qq} 的分析结果已生成")
    return _me(qq)


@router.get("/result/{kind}")
async def result_image(kind: str, qq: str = Depends(_current_qq)):
    if kind not in RESULT_FILES:
        raise HTTPException(404)
    path = user_output_dir(qq) / RESULT_FILES[kind]
    if not path.exists():
        raise HTTPException(404, "结果图不存在，可能已被清理")
    return FileResponse(path, media_type="image/png")


nonebot.get_app().include_router(router)
