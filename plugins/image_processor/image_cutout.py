# image_cutout.py
import asyncio
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from nonebot import get_driver, logger
from PIL import Image

from core.lifecycle import on_plugin_startup
from utils.files import ensure_output_dir, safe_delete_file
from utils.network.http import download_to_file
from plugins.image_processor.utils import (
    UserInputError,
    download_image,
    load_gif_frames,
    open_static_image,
    run_command,
    save_gif,
)

# ========= 纯色背景算法参数（面向“贴纸/表情包 + 纯色背景 + 文字”） =========
# 角落采样块大小（像素）
SOLID_BG_CORNER_SIZE = 12

# 认为“近纯色背景”的阈值（LAB 空间标准差，越小越严格）
SOLID_BG_MAX_STD = 8.0

# 四角背景色互相接近的阈值（LAB 空间距离）
SOLID_BG_MAX_CORNER_DELTA = 12.0

# 与背景色距离小于该阈值的像素视为背景候选（LAB 空间距离）
# 这个值越大越“敢删背景”，但也更容易从抗锯齿边缘“渗入”前景造成缺块
SOLID_BG_DIST_THRESHOLD = 16.0

# 边缘保护：Canny 边缘膨胀半径（迭代次数）
EDGE_DILATE_ITERS = 1

# 边缘屏障：用于阻止“背景连通域”穿过主体边缘渗入内部（解决头顶高光被挖洞）
EDGE_BARRIER_ITERS = 2

# 渗漏检测：若边缘屏障区域被判为背景的比例过高，则认为纯色抠图失败，交给模型
EDGE_BG_LEAK_MAX_RATIO = 0.12

# 形态学平滑（填小洞 + 连接细笔画）
MORPH_CLOSE_ITERS = 1
MORPH_DILATE_ITERS = 1

# alpha 羽化：高斯模糊 sigma（0 表示不羽化）
ALPHA_FEATHER_SIGMA = 1.0

# 结果 sanity check：前景比例过小/过大直接判失败
MIN_FOREGROUND_RATIO = 0.01
MAX_FOREGROUND_RATIO = 0.98

# 已有透明图快速返回阈值
EXISTING_ALPHA_TRANSPARENT_RATIO = 0.01

# GIF 逐帧只走传统算法，帧数限制避免拖垮 bot
CUTOUT_GIF_MAX_FRAMES = int(os.getenv("HAKUBOT_CUTOUT_GIF_MAX_FRAMES", "80"))

# ========= 模型 =========
# 换模型要同步改 isnet_worker.py 的预处理参数
MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "shared"
    / "rembg_models"
    / "isnet-anime.onnx"
)
MODEL_URL = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-anime.onnx"
)
WORKER_PATH = Path(__file__).parent / "isnet_worker.py"
ISNET_TIMEOUT = 60
# 每次推理峰值约 1GB，小机器上同时只跑一个，后来的请求排队
_ISNET_LOCK = asyncio.Lock()


# 挂在 cutout 功能上：下载失败只让抠图不可用，bot 照常启动
@on_plugin_startup(get_driver(), "image_processor:cutout")
async def _ensure_model() -> None:
    # download_to_file 先写临时文件再替换，文件存在即完整
    if MODEL_PATH.exists():
        return
    logger.info(f"下载抠图模型: {MODEL_URL}")
    await download_to_file(
        MODEL_URL, MODEL_PATH, max_bytes=300 * 1024 * 1024, timeout=600
    )
    logger.info(f"抠图模型下载完成: {MODEL_PATH}")


def _to_bgr(img: Image.Image) -> np.ndarray:
    """带透明的图先合到白底，保证颜色特征稳定（尤其是贴纸）"""
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(white, img.convert("RGBA")).convert("RGB")
    return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)


def _estimate_bg_lab(img_bgr: np.ndarray) -> tuple[np.ndarray, bool]:
    """
    用四角采样估计背景色（LAB），并判断是否为“近纯色背景”。
    返回：
      (bg_lab_mean(3,), is_solid_bg)
    """
    h, w = img_bgr.shape[:2]
    s = int(max(4, min(SOLID_BG_CORNER_SIZE, min(h, w) // 6)))

    corners = [
        img_bgr[0:s, 0:s],
        img_bgr[0:s, w - s : w],
        img_bgr[h - s : h, 0:s],
        img_bgr[h - s : h, w - s : w],
    ]

    # BGR -> LAB
    corner_labs = []
    corner_stds = []
    for c in corners:
        lab = cv2.cvtColor(c, cv2.COLOR_BGR2LAB).astype(np.float32)
        corner_labs.append(lab.reshape(-1, 3).mean(axis=0))
        corner_stds.append(lab.reshape(-1, 3).std(axis=0).mean())

    corner_labs = np.stack(corner_labs, axis=0)  # (4,3)
    corner_stds = np.array(corner_stds, dtype=np.float32)  # (4,)

    # 近纯色：角落内部方差小 + 四角均值相互接近
    is_low_var = float(corner_stds.max()) <= SOLID_BG_MAX_STD

    # 角落间最大距离
    max_delta = 0.0
    for i in range(4):
        for j in range(i + 1, 4):
            d = float(np.linalg.norm(corner_labs[i] - corner_labs[j]))
            max_delta = max(max_delta, d)

    is_similar = max_delta <= SOLID_BG_MAX_CORNER_DELTA
    is_solid = is_low_var and is_similar

    bg_lab = corner_labs.mean(axis=0)
    return bg_lab.astype(np.float32), is_solid


def _connected_bg_from_border(bg_candidate: np.ndarray) -> np.ndarray:
    """
    仅把“与图像边界连通”的背景候选当作背景（避免误删前景内部的浅色块）。
    bg_candidate: uint8, 1=候选背景, 0=非背景
    return: uint8, 1=背景, 0=非背景
    """
    h, w = bg_candidate.shape[:2]
    num, labels = cv2.connectedComponents(bg_candidate, connectivity=8)

    if num <= 1:
        return bg_candidate

    border = np.concatenate(
        [
            labels[0, :],
            labels[h - 1, :],
            labels[:, 0],
            labels[:, w - 1],
        ]
    )
    border_labels = np.unique(border)
    # label=0 是背景（bg_candidate==0 的区域），不要选
    border_labels = border_labels[border_labels != 0]

    if border_labels.size == 0:
        # 没有任何候选与边界连通，说明阈值过严，返回全 0（让上层判失败）
        return np.zeros_like(bg_candidate)

    return np.isin(labels, border_labels).astype(np.uint8)


def _mask_to_rgba(img_bgr: np.ndarray, fg_mask: np.ndarray) -> Image.Image:
    """fg_mask: uint8 {0,1}"""
    fg = (fg_mask > 0).astype(np.uint8)

    # 形态学：连接细笔画、填小洞
    kernel = np.ones((3, 3), np.uint8)
    if MORPH_CLOSE_ITERS > 0:
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel, iterations=MORPH_CLOSE_ITERS)
    if MORPH_DILATE_ITERS > 0:
        fg = cv2.dilate(fg, kernel, iterations=MORPH_DILATE_ITERS)

    alpha = (fg * 255).astype(np.uint8)
    if ALPHA_FEATHER_SIGMA > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=ALPHA_FEATHER_SIGMA)

    rgba = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGBA)
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def _solid_bg_cutout(img: Image.Image) -> Image.Image | None:
    """
    贴纸/表情包专用：针对近纯色背景的高保真抠图，细小文字/描边保留得比模型好。
    背景不是纯色或判定不可靠时返回 None。
    """
    img_bgr = _to_bgr(img)
    h, w = img_bgr.shape[:2]

    bg_lab, is_solid = _estimate_bg_lab(img_bgr)
    if not is_solid:
        return None

    # 先做边缘检测，构造“边缘屏障”，防止背景连通域从抗锯齿边缘渗入前景内部
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    edge_kernel = np.ones((3, 3), np.uint8)
    edge_barrier = edges
    if EDGE_BARRIER_ITERS > 0:
        edge_barrier = cv2.dilate(
            edge_barrier, edge_kernel, iterations=EDGE_BARRIER_ITERS
        )
    edge_barrier = (edge_barrier > 0).astype(np.uint8)

    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    dist = np.linalg.norm(lab - bg_lab.reshape(1, 1, 3), axis=2)

    # 低对比场景检测：如果主体也接近背景色，纯色阈值分割会“只剩描边”
    # 用中心区域（通常是主体所在）做对比度判定
    ch, cw = max(1, h // 4), max(1, w // 4)
    center = dist[ch : h - ch, cw : w - cw]
    if center.size > 0:
        # 中心区域如果连 90 分位都不够“远离背景”，说明前景/背景不可分
        p90 = float(np.percentile(center, 90))
        if p90 < SOLID_BG_DIST_THRESHOLD * 1.25:
            return None

    bg_candidate = (dist < SOLID_BG_DIST_THRESHOLD).astype(np.uint8)

    # 关键：边缘作为“墙”，不允许 bg_candidate 通过边缘连通到主体内部
    bg_candidate = (bg_candidate & (1 - edge_barrier)).astype(np.uint8)

    bg = _connected_bg_from_border(bg_candidate)

    # 渗漏检测：如果边缘屏障区域大量被判成背景，说明发生了“穿透”，交给模型
    if int(edge_barrier.sum()) > 0:
        leak_ratio = float(bg[edge_barrier > 0].mean())
        if leak_ratio > EDGE_BG_LEAK_MAX_RATIO:
            return None

    fg = (1 - bg).astype(np.uint8)

    # 边缘保护：把明显边缘像素强制视为前景，避免阈值误杀细笔画/浅色字边缘
    protect = edges
    if EDGE_DILATE_ITERS > 0:
        protect = cv2.dilate(protect, edge_kernel, iterations=EDGE_DILATE_ITERS)
    protect_bin = (protect > 0).astype(np.uint8)
    fg = np.maximum(fg, protect_bin)

    # “只剩描边”检测：如果去掉边缘保护后，核心前景几乎没有，交给模型
    core = (fg & (1 - protect_bin)).astype(np.uint8)
    if float(core.mean()) < 0.02:
        return None

    fg_ratio = float(fg.mean())
    if fg_ratio < MIN_FOREGROUND_RATIO or fg_ratio > MAX_FOREGROUND_RATIO:
        return None

    return _mask_to_rgba(img_bgr, fg)


def _existing_alpha(img: Image.Image) -> Image.Image | None:
    """已经抠好的透明图直接返回"""
    if img.mode not in ("RGBA", "LA", "P"):
        return None
    rgba = img.convert("RGBA")
    transparent_ratio = float((np.asarray(rgba)[:, :, 3] < 250).mean())
    if transparent_ratio < EXISTING_ALPHA_TRANSPARENT_RATIO:
        return None
    return rgba


def _cutout_frame(img: Image.Image) -> Image.Image | None:
    """已有透明 → 纯色背景；都不适用时返回 None"""
    return _existing_alpha(img) or _solid_bg_cutout(img)


def _cutout_static_file(image_path: str, output_path: Path) -> bool:
    """传统算法能处理就写出结果；返回 False 表示要交给模型"""
    result = _cutout_frame(open_static_image(image_path))
    if result is None:
        return False
    result.save(output_path, "PNG")
    return True


def _cutout_gif_file(image_path: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if len(frames) > CUTOUT_GIF_MAX_FRAMES:
        raise UserInputError(f"GIF帧数过多: {len(frames)} > {CUTOUT_GIF_MAX_FRAMES}")

    results = []
    for frame in frames:
        result = _cutout_frame(frame)
        if result is None:
            raise UserInputError("GIF 背景不是纯色，无法抠图")
        results.append(result)

    output_dir = ensure_output_dir("nonebot_image_cutout")
    output_path = output_dir / f"cutout_gif_{os.urandom(4).hex()}.gif"
    save_gif(results, output_path, durations=durations, loop=int(meta["loop"]))
    return str(output_path)


async def _isnet_cutout(image_path: str, output_path: Path) -> None:
    async with _ISNET_LOCK:
        logger.debug("抠图: 走 isnet-anime 模型推理")
        await run_command(
            [
                sys.executable,
                str(WORKER_PATH),
                str(MODEL_PATH),
                image_path,
                str(output_path),
            ],
            timeout=ISNET_TIMEOUT,
            label="模型推理",
        )


async def remove_background(image_url: str) -> str:
    """主抠图函数：静态图 已有透明 → 纯色背景 → isnet-anime；GIF 只走前两步"""
    async with download_image(image_url) as (image_path, animated):
        if animated:
            return await asyncio.to_thread(_cutout_gif_file, image_path)

        output_dir = ensure_output_dir("nonebot_image_cutout")
        output_path = output_dir / f"cutout_{os.urandom(4).hex()}.png"
        try:
            if not await asyncio.to_thread(
                _cutout_static_file, image_path, output_path
            ):
                await _isnet_cutout(image_path, output_path)
        except BaseException:
            safe_delete_file(output_path)
            raise
        return str(output_path)
