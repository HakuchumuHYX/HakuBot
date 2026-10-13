"""isnet-anime 抠图推理，由 image_cutout 在子进程里调用

推理峰值内存约 1GB，放在子进程里跑完就能完整还给系统。
用法：python isnet_worker.py <模型路径> <输入图片> <输出 PNG>
"""

import sys

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

MODEL_SIZE = 1024
# 与 rembg 对 isnet-anime 的预处理一致
MEAN = np.array([0.485, 0.456, 0.406], np.float32)


def main(model_path: str, input_path: str, output_path: str) -> None:
    options = ort.SessionOptions()
    # 内存池会按峰值预留内存，关掉后峰值从约 1.5GB 降到约 1GB
    options.enable_cpu_mem_arena = False
    session = ort.InferenceSession(
        model_path, options, providers=["CPUExecutionProvider"]
    )

    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
    resized = img.resize((MODEL_SIZE, MODEL_SIZE), Image.Resampling.LANCZOS)
    x = (np.asarray(resized, np.float32) / 255 - MEAN).transpose(2, 0, 1)[None]
    pred = session.run(None, {session.get_inputs()[0].name: x})[0][0, 0]
    pred = (pred - pred.min()) / (pred.max() - pred.min())
    mask = Image.fromarray((pred * 255).astype(np.uint8)).resize(
        img.size, Image.Resampling.LANCZOS
    )
    img.putalpha(mask)
    img.save(output_path, "PNG")


if __name__ == "__main__":
    main(*sys.argv[1:])
