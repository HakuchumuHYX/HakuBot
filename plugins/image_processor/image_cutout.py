# image_cutout.py
import os

import io
import tempfile
from pathlib import Path

import aiohttp
import cv2
import numpy as np
from PIL import Image, ImageSequence
from nonebot.log import logger

# ========= 可调参数（面向“贴纸/表情包 + 纯色背景 + 文字”） =========
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

# 渗漏检测：若边缘屏障区域被判为背景的比例过高，则认为纯色抠图失败，回退 rembg
EDGE_BG_LEAK_MAX_RATIO = 0.12

# 形态学平滑（填小洞 + 连接细笔画）
MORPH_CLOSE_ITERS = 1
MORPH_DILATE_ITERS = 1

# alpha 羽化：高斯模糊 sigma（0 表示不羽化）
ALPHA_FEATHER_SIGMA = 1.0

# 结果 sanity check：前景比例过小直接判失败（避免把整图抠没）
MIN_FOREGROUND_RATIO = 0.01

# rembg 输入可选放大（提升细节保留）；设置为 1 表示禁用
REMBG_UPSCALE = 2
REMBG_UPSCALE_MAX_SIDE = 1600

# rembg 模型：
# - 对二次元/线稿类图片，isnet-anime 往往比 isnet-general-use 更稳
# - 纯色背景贴纸通常会走 solid-bg，不太依赖 rembg
REMBG_MODEL_PRIMARY = os.getenv("HAKUBOT_REMBG_MODEL", "isnet-anime").strip().lower()
REMBG_MODEL_FALLBACK = os.getenv("HAKUBOT_REMBG_MODEL_FALLBACK", "isnet-general-use").strip().lower()

# rembg 推理设备选择：
# - auto: 有 CUDAExecutionProvider 就用 GPU，否则 CPU
# - cpu: 强制 CPU
# - cuda/gpu: 强制 GPU（若不可用会回退 CPU）
REMBG_DEVICE = os.getenv("HAKUBOT_REMBG_DEVICE", "auto").strip().lower()

# rembg 质量检测：alpha 太“边缘化/过小”就尝试 fallback 模型
REMBG_MIN_FG_RATIO = 0.03
REMBG_MIN_CORE_RATIO = 0.012

# 线稿/描边类 fallback（适用于“背景和主体很接近，但黑色描边明显”的表情包）
LINEART_MIN_AREA_RATIO = 0.03
LINEART_MAX_AREA_RATIO = 0.98
LINEART_MIN_CONTOUR_AREA = 200
LINEART_EDGE_CLOSE_ITERS = 2
LINEART_EDGE_DILATE_ITERS = 2


def _select_onnx_providers() -> list[str]:
    """为 rembg/new_session 选择 onnxruntime providers（支持 CUDA 自动探测）。"""
    try:
        import onnxruntime as ort  # type: ignore

        available = set(ort.get_available_providers())
    except Exception as e:
        logger.warning(f"onnxruntime providers 探测失败，将使用 CPU: {e}")
        return ["CPUExecutionProvider"]

    if REMBG_DEVICE in ("cpu",):
        # 这两行可显著避免某些环境里误加载 CUDA 相关 DLL
        os.environ["ORT_DISABLE_CUDA"] = "1"
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
        return ["CPUExecutionProvider"]

    if REMBG_DEVICE in ("cuda", "gpu"):
        if "CUDAExecutionProvider" in available:
            # GPU 优先，CPU 兜底
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        logger.warning("当前 onnxruntime 未提供 CUDAExecutionProvider，已回退到 CPU")
        return ["CPUExecutionProvider"]

    # auto
    if "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


async def download_image(url: str) -> str:
    """下载图片到临时目录"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"下载图片失败: {response.status}")

            temp_dir = tempfile.gettempdir()
            file_ext = ".png"
            temp_path = os.path.join(
                temp_dir, f"temp_img_{os.urandom(4).hex()}{file_ext}"
            )

            content = await response.read()
            with open(temp_path, "wb") as f:
                f.write(content)

            return temp_path


def _read_cv_bgr(image_path: str) -> np.ndarray:
    """
    读取图片为 BGR（uint8）。
    - 如果源图带 alpha，会先按 alpha 合成到白底，保证颜色特征稳定（尤其是贴纸）。
    """
    img = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise Exception("无法读取图像")

    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    if img.shape[2] == 4:
        bgr = img[:, :, :3].astype(np.float32)
        alpha = (img[:, :, 3:4].astype(np.float32)) / 255.0
        white = np.full_like(bgr, 255.0)
        comp = bgr * alpha + white * (1.0 - alpha)
        return comp.clip(0, 255).astype(np.uint8)

    return img[:, :, :3]


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

    bg = np.isin(labels, border_labels).astype(np.uint8)
    return bg


def _mask_to_rgba(img_bgr: np.ndarray, fg_mask: np.ndarray) -> np.ndarray:
    """
    fg_mask: uint8 {0,1}
    返回 BGRA uint8
    """
    fg = (fg_mask > 0).astype(np.uint8)

    # 形态学：连接细笔画、填小洞
    kernel = np.ones((3, 3), np.uint8)
    if MORPH_CLOSE_ITERS > 0:
        fg = cv2.morphologyEx(
            fg, cv2.MORPH_CLOSE, kernel, iterations=MORPH_CLOSE_ITERS
        )
    if MORPH_DILATE_ITERS > 0:
        fg = cv2.dilate(fg, kernel, iterations=MORPH_DILATE_ITERS)

    alpha = (fg * 255).astype(np.uint8)
    if ALPHA_FEATHER_SIGMA and ALPHA_FEATHER_SIGMA > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=ALPHA_FEATHER_SIGMA)

    bgra = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = alpha
    return bgra


def _alpha_quality_from_png_path(png_path: str) -> tuple[float, float]:
    """
    对输出 PNG 的 alpha 做一个粗略“质量评估”，用于检测“只剩描边/前景过小”。
    返回：(fg_ratio, core_ratio)
      - fg_ratio: alpha>8 的比例
      - core_ratio: 对 fg 侵蚀 3 次后仍为前景的比例（越大越像“有实体填充”）
    """
    try:
        im_out = Image.open(png_path).convert("RGBA")
        alpha = np.array(im_out)[:, :, 3]
        fg = (alpha > 8).astype(np.uint8)
        fg_ratio = float(fg.mean())
        core = fg
        if fg_ratio > 0:
            core = cv2.erode(core, np.ones((3, 3), np.uint8), iterations=3)
        core_ratio = float(core.mean())
        return fg_ratio, core_ratio
    except Exception:
        return 0.0, 0.0


async def remove_background_lineart(image_path: str) -> str:
    """
    线稿/描边类表情包兜底：
    - 当 rembg 也只抠出线条时，用“线条作墙 + flood fill”把封闭区域填成前景。
    适用于：背景与主体颜色接近、但黑色/深色描边很明显的图。
    """
    try:
        img_bgr = _read_cv_bgr(image_path)
        h, w = img_bgr.shape[:2]

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # 用自适应阈值抓“深色描边”（比单纯 Canny 稳定一些）
        line = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV,
            15,
            8,
        )

        # 再叠加一点边缘，补一些断开的线
        edges = cv2.Canny(gray, 50, 150)
        walls = cv2.bitwise_or(line, edges)

        kernel = np.ones((3, 3), np.uint8)

        if LINEART_EDGE_CLOSE_ITERS and LINEART_EDGE_CLOSE_ITERS > 0:
            walls = cv2.morphologyEx(
                walls, cv2.MORPH_CLOSE, kernel, iterations=LINEART_EDGE_CLOSE_ITERS
            )
        if LINEART_EDGE_DILATE_ITERS and LINEART_EDGE_DILATE_ITERS > 0:
            walls = cv2.dilate(walls, kernel, iterations=LINEART_EDGE_DILATE_ITERS)

        walls = (walls > 0).astype(np.uint8)

        # free: 255 表示可通行区域（非墙），0 表示墙
        free = ((1 - walls) * 255).astype(np.uint8)

        # flood fill：从图像边界把外部区域填成 0，剩下的 255 就是“被墙围起来的内部”
        mask = np.zeros((h + 2, w + 2), np.uint8)

        # 多个种子点，提高鲁棒性
        seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1)]
        for x, y in seeds:
            if free[y, x] == 255:
                cv2.floodFill(free, mask, (x, y), 0)

        inside = (free == 255).astype(np.uint8)

        # 前景 = inside + walls
        fg = np.maximum(inside, walls).astype(np.uint8)

        area_ratio = float(fg.mean())
        if not (LINEART_MIN_AREA_RATIO <= area_ratio <= LINEART_MAX_AREA_RATIO):
            return ""

        rgba = _mask_to_rgba(img_bgr, fg)

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_lineart_{os.urandom(4).hex()}.png"
        cv2.imwrite(str(output_path), rgba)
        return str(output_path)

    except Exception as e:
        logger.error(f"线稿兜底抠图错误: {e}")
        return ""


async def remove_background_solid_bg(image_path: str) -> str:
    """
    贴纸/表情包专用：针对近纯色背景的高保真抠图。
    能显著提升“细小文字/描边”保留率，解决 u2net 容易把字抠没的问题。
    """
    try:
        img_bgr = _read_cv_bgr(image_path)
        h, w = img_bgr.shape[:2]

        bg_lab, is_solid = _estimate_bg_lab(img_bgr)
        if not is_solid:
            return ""

        # 先做边缘检测，构造“边缘屏障”，防止背景连通域从抗锯齿边缘渗入前景内部
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)

        edge_kernel = np.ones((3, 3), np.uint8)
        edge_barrier = edges
        if EDGE_BARRIER_ITERS and EDGE_BARRIER_ITERS > 0:
            edge_barrier = cv2.dilate(edge_barrier, edge_kernel, iterations=EDGE_BARRIER_ITERS)
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
                return ""

        bg_candidate = (dist < SOLID_BG_DIST_THRESHOLD).astype(np.uint8)

        # 关键：边缘作为“墙”，不允许 bg_candidate 通过边缘连通到主体内部
        bg_candidate = (bg_candidate & (1 - edge_barrier)).astype(np.uint8)

        bg = _connected_bg_from_border(bg_candidate)

        # 渗漏检测：如果边缘屏障区域大量被判成背景，说明发生了“穿透”，直接失败回退 rembg
        barrier_pixels = int(edge_barrier.sum())
        if barrier_pixels > 0:
            leak_ratio = float(bg[edge_barrier > 0].mean())
            if leak_ratio > EDGE_BG_LEAK_MAX_RATIO:
                return ""

        fg = (1 - bg).astype(np.uint8)

        # 边缘保护：把明显边缘像素强制视为前景，避免阈值误杀细笔画/浅色字边缘
        protect = edges
        if EDGE_DILATE_ITERS and EDGE_DILATE_ITERS > 0:
            protect = cv2.dilate(protect, edge_kernel, iterations=EDGE_DILATE_ITERS)
        protect_bin = (protect > 0).astype(np.uint8)
        fg = np.maximum(fg, protect_bin)

        # “只剩描边”检测：如果去掉边缘保护后，核心前景几乎没有，则认为失败回退 rembg
        core = (fg & (1 - protect_bin)).astype(np.uint8)
        if float(core.mean()) < 0.02:
            return ""

        fg_ratio = float(fg.mean())
        if fg_ratio < MIN_FOREGROUND_RATIO:
            return ""

        rgba = _mask_to_rgba(img_bgr, fg)

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_solid_{os.urandom(4).hex()}.png"
        cv2.imwrite(str(output_path), rgba)

        return str(output_path)
    except Exception as e:
        logger.error(f"纯色背景抠图错误: {e}")
        return ""


async def remove_background_rembg(image_path: str) -> str:
    """使用 rembg 背景移除（用于非纯色背景兜底）"""
    try:
        from rembg import remove
        from rembg.session_factory import new_session

        providers = _select_onnx_providers()
        logger.info(f"rembg providers: {providers} (device={REMBG_DEVICE})")

        # 可选：放大输入以保留细节（对文字/线条更友好）
        with Image.open(image_path) as im:
            im = im.convert("RGBA")
            orig_size = im.size

            upscale = int(max(1, REMBG_UPSCALE))
            if upscale > 1:
                max_side = max(orig_size)
                if max_side * upscale > REMBG_UPSCALE_MAX_SIDE:
                    upscale = max(1, int(REMBG_UPSCALE_MAX_SIDE / max_side))

            if upscale > 1:
                im = im.resize(
                    (orig_size[0] * upscale, orig_size[1] * upscale), Image.LANCZOS
                )

            buf = io.BytesIO()
            im.save(buf, format="PNG")
            input_data = buf.getvalue()

        def _run_model(model_name: str) -> bytes:
            logger.info(f"rembg model: {model_name}")
            session = new_session(model_name, providers=providers)
            return remove(input_data, session=session)

        def _score_alpha(png_bytes: bytes) -> tuple[float, float]:
            """返回 (fg_ratio, core_ratio)"""
            im_out = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
            alpha = np.array(im_out)[:, :, 3]
            fg = (alpha > 8).astype(np.uint8)
            fg_ratio = float(fg.mean())

            # core: 侵蚀后仍为前景的比例，用于检测“只剩描边”
            core = fg
            if fg_ratio > 0:
                core = cv2.erode(core, np.ones((3, 3), np.uint8), iterations=3)
            core_ratio = float(core.mean())
            return fg_ratio, core_ratio

        # 第一次：primary
        out1 = _run_model(REMBG_MODEL_PRIMARY)
        fg1, core1 = _score_alpha(out1)

        # 如果出现“只剩描边/前景过小”，再跑 fallback
        out_best, best = out1, (fg1, core1)
        if fg1 < REMBG_MIN_FG_RATIO or core1 < REMBG_MIN_CORE_RATIO:
            out2 = _run_model(REMBG_MODEL_FALLBACK)
            fg2, core2 = _score_alpha(out2)

            # 选择 core 更大的结果（更像“有实体填充”），core 相同时选 fg 更大的
            if (core2, fg2) > (best[1], best[0]):
                out_best, best = out2, (fg2, core2)

        output_data = out_best

        # 如果做过放大，需要缩回去
        if upscale > 1:
            out_im = Image.open(io.BytesIO(output_data)).convert("RGBA")
            out_im = out_im.resize(orig_size, Image.LANCZOS)
            out_buf = io.BytesIO()
            out_im.save(out_buf, format="PNG")
            output_data = out_buf.getvalue()

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_rembg_{os.urandom(4).hex()}.png"

        with open(output_path, "wb") as output_file:
            output_file.write(output_data)

        return str(output_path)

    except ImportError:
        logger.error("rembg未安装，请安装: pip install rembg onnxruntime")
        return ""
    except Exception as e:
        logger.error(f"rembg抠图错误: {e}")
        return ""


async def remove_background_opencv(image_path: str) -> str:
    """使用 OpenCV 的 GrabCut（最终兜底）"""
    try:
        img_bgr = _read_cv_bgr(image_path)
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

        mask = np.zeros(img_rgb.shape[:2], np.uint8)
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)

        height, width = img_rgb.shape[:2]
        rect = (int(width * 0.08), int(height * 0.08), int(width * 0.84), int(height * 0.84))

        cv2.grabCut(img_rgb, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)

        # 0/2 是背景，1/3 是前景
        fg = np.where((mask == 0) | (mask == 2), 0, 1).astype("uint8")

        if float(fg.mean()) < MIN_FOREGROUND_RATIO:
            return ""

        rgba = _mask_to_rgba(cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR), fg)

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_cv_{os.urandom(4).hex()}.png"
        cv2.imwrite(str(output_path), rgba)

        return str(output_path)
    except Exception as e:
        logger.error(f"OpenCV抠图错误: {e}")
        return ""


async def remove_background_simple(image_path: str) -> str:
    """
    最简方案兜底（尽量不要走到这里）
    通过检测近白背景并保留边缘。
    """
    try:
        img = Image.open(image_path).convert("RGBA")
        img_array = np.array(img)

        r, g, b, a = (
            img_array[:, :, 0],
            img_array[:, :, 1],
            img_array[:, :, 2],
            img_array[:, :, 3],
        )

        white_threshold = 235
        white_mask = (r > white_threshold) & (g > white_threshold) & (b > white_threshold)

        gray = cv2.cvtColor(img_array[:, :, :3], cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        edge_mask = edges > 0

        background_mask = white_mask & ~edge_mask
        img_array[background_mask] = [0, 0, 0, 0]

        result_img = Image.fromarray(img_array, "RGBA")

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_simple_{os.urandom(4).hex()}.png"
        result_img.save(output_path, "PNG")

        return str(output_path)
    except Exception as e:
        logger.error(f"简单抠图错误: {e}")
        return ""


async def remove_background_file(image_path: str) -> str:
    """对本地文件做抠图：纯色背景优先 -> rembg -> grabcut -> simple"""
    # 1) 贴纸最常见：纯/近纯色背景
    solid = await remove_background_solid_bg(image_path)
    if solid and os.path.exists(solid):
        return solid

    # 2) rembg 兜底（需要依赖）
    rem = await remove_background_rembg(image_path)
    if rem and os.path.exists(rem):
        # 如果 rembg 结果“只剩描边/前景过小”，尝试线稿兜底（对表情包更友好）
        fg_ratio, core_ratio = _alpha_quality_from_png_path(rem)
        if fg_ratio < REMBG_MIN_FG_RATIO or core_ratio < REMBG_MIN_CORE_RATIO:
            lineart = await remove_background_lineart(image_path)
            if lineart and os.path.exists(lineart):
                return lineart
        return rem

    # 3) OpenCV GrabCut 兜底
    cvp = await remove_background_opencv(image_path)
    if cvp and os.path.exists(cvp):
        return cvp

    # 4) 最简兜底
    simp = await remove_background_simple(image_path)
    if simp and os.path.exists(simp):
        return simp

    return ""


async def remove_background_gif(image_path: str) -> str:
    """处理 GIF 抠图 - 逐帧处理（复用同样的策略）"""
    try:
        gif = Image.open(image_path)
        frames: list[Image.Image] = []
        durations: list[int] = []

        for frame in ImageSequence.Iterator(gif):
            frame_rgba = frame.convert("RGBA")
            temp_frame_path = os.path.join(
                tempfile.gettempdir(), f"temp_frame_{os.urandom(4).hex()}.png"
            )
            frame_rgba.save(temp_frame_path, "PNG")

            processed_frame_path = ""
            try:
                processed_frame_path = await remove_background_file(temp_frame_path)
                if processed_frame_path and os.path.exists(processed_frame_path):
                    processed_frame = Image.open(processed_frame_path).convert("RGBA")
                    frames.append(processed_frame)
                    durations.append(frame.info.get("duration", 100))
            finally:
                # 清理临时文件
                if os.path.exists(temp_frame_path):
                    os.unlink(temp_frame_path)
                if processed_frame_path and os.path.exists(processed_frame_path):
                    os.unlink(processed_frame_path)

        if not frames:
            raise Exception("没有成功处理的帧")

        output_dir = Path(tempfile.gettempdir()) / "nonebot_image_cutout"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"cutout_gif_{os.urandom(4).hex()}.gif"

        frames[0].save(
            str(output_path),
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            format="GIF",
            disposal=2,
            transparency=0,
        )

        return str(output_path)

    except Exception as e:
        logger.error(f"GIF抠图错误: {e}")
        # GIF 失败时退回静态抠图（可能是伪 GIF 或解析失败）
        return await remove_background_file(image_path)


async def remove_background(image_url: str) -> str:
    """主抠图函数 - 支持静态图片和 GIF"""
    image_path = ""
    try:
        image_path = await download_image(image_url)

        # 是否为 GIF
        is_gif = False
        try:
            with Image.open(image_path) as img:
                is_gif = bool(getattr(img, "is_animated", False))
        except Exception:
            is_gif = False

        if is_gif:
            result_path = await remove_background_gif(image_path)
        else:
            result_path = await remove_background_file(image_path)

        return result_path if result_path and os.path.exists(result_path) else ""

    except Exception as e:
        logger.error(f"抠图处理错误: {e}")
        return ""
    finally:
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except Exception:
                pass
