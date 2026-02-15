import asyncio
import os
from pathlib import Path
from typing import Iterable

import aiohttp
from nonebot.log import logger


REMBG_RELEASE_BASE_URL = os.getenv(
    "HAKUBOT_REMBG_RELEASE_BASE_URL",
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/",
).rstrip("/") + "/"


def get_u2net_home() -> Path:
    """
    rembg 默认会把模型下载到 ~/.u2net（Windows 通常在 C 盘用户目录）。
    这里把默认路径改为：项目根目录下的 data/rembg_models，避免占用系统盘。

    仍可通过环境变量 U2NET_HOME 覆盖。
    """
    env = os.getenv("U2NET_HOME", "").strip()
    if env:
        return Path(env)

    # rembg_prefetch.py 位于 plugins/image_processor 下
    project_root = Path(__file__).resolve().parents[2]
    default_home = project_root / "data" / "rembg_models"

    # 确保同一进程里 rembg 推理也使用该路径
    os.environ.setdefault("U2NET_HOME", str(default_home))

    return default_home


def _model_filename(model_name: str) -> str:
    # rembg 的 onnx 模型命名规则基本是 {model_name}.onnx
    return f"{model_name}.onnx"


def _model_url(model_name: str) -> str:
    return f"{REMBG_RELEASE_BASE_URL}{_model_filename(model_name)}"


async def _head_content_length(session: aiohttp.ClientSession, url: str) -> int | None:
    try:
        async with session.head(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return None
            v = resp.headers.get("Content-Length")
            return int(v) if v and v.isdigit() else None
    except Exception:
        return None


async def ensure_rembg_models_downloaded(
    model_names: Iterable[str],
    *,
    timeout_sec: int = 60,
    retries: int = 3,
    strict: bool = False,
) -> None:
    """
    启动时确保 rembg 模型已完整下载。
    - 若发现文件存在但小于期望 Content-Length，会删除并重新下载。
    - 下载使用临时文件 + 原子替换，避免留下半截文件。
    """
    names = [m.strip().lower() for m in model_names if m and m.strip()]
    if not names:
        return

    u2net_home = get_u2net_home()
    u2net_home.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in names:
            url = _model_url(model)
            target = u2net_home / _model_filename(model)

            expected_len = await _head_content_length(session, url)

            # 如果已有文件且看起来完整，就跳过
            if target.exists() and target.is_file():
                try:
                    size = target.stat().st_size
                    if expected_len is None:
                        # 没拿到长度时做一个保守判断：>1MB 认为不是空文件
                        if size > 1_000_000:
                            logger.info(f"[rembg] 模型已存在: {target} ({size} bytes)")
                            continue
                    else:
                        if size == expected_len:
                            logger.info(f"[rembg] 模型已存在且完整: {target} ({size} bytes)")
                            continue
                        if size > expected_len:
                            # 极少数情况：重复写入等，直接保留并跳过
                            logger.warning(
                                f"[rembg] 模型大小异常(>expected)，保留现有文件: {target} ({size}>{expected_len})"
                            )
                            continue

                    # 不完整：删除并重下
                    logger.warning(
                        f"[rembg] 检测到模型不完整，将重新下载: {target} ({size}/{expected_len or 'unknown'})"
                    )
                    try:
                        target.unlink(missing_ok=True)
                    except Exception as e:
                        logger.warning(f"[rembg] 删除不完整模型失败: {target}: {e}")
                except Exception:
                    pass

            ok = False
            last_err: Exception | None = None

            for attempt in range(1, retries + 1):
                tmp = u2net_home / (target.name + f".part-{os.urandom(3).hex()}")
                try:
                    logger.info(f"[rembg] 下载模型({attempt}/{retries}): {model} <- {url}")

                    async with session.get(url, allow_redirects=True) as resp:
                        resp.raise_for_status()
                        downloaded = 0
                        with open(tmp, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1024 * 256):
                                if not chunk:
                                    continue
                                f.write(chunk)
                                downloaded += len(chunk)

                    # 基本完整性校验
                    if expected_len is not None and downloaded != expected_len:
                        raise IOError(
                            f"incomplete download: got {downloaded}, expected {expected_len}"
                        )
                    if downloaded < 1_000_000:
                        raise IOError(f"downloaded file too small: {downloaded} bytes")

                    # 原子替换
                    tmp.replace(target)
                    logger.info(f"[rembg] 模型下载完成: {target} ({downloaded} bytes)")
                    ok = True
                    break

                except Exception as e:
                    last_err = e
                    logger.warning(f"[rembg] 下载失败: {model}: {e}")
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
                    # 退避重试
                    await asyncio.sleep(min(5 * attempt, 15))

            if not ok:
                msg = f"[rembg] 模型下载失败（已重试 {retries} 次）: {model} ({url})"
                if strict:
                    raise RuntimeError(msg) from last_err
                logger.error(msg)
