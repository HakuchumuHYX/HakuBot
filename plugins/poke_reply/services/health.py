import json
from pathlib import Path

from nonebot import logger

from ..config import IMAGE_FILES_DIR, TEXT_FILES_DIR


def scan_poke_reply_data_health():
    report = {
        "text_files": {
            "checked": 0,
            "invalid": [],
            "abnormal_names": [],
            "non_string_items": [],
            "blank_items": [],
        },
        "image_lists": {
            "checked": 0,
            "invalid": [],
            "abnormal_names": [],
            "missing_refs": [],
        },
    }

    for text_path in sorted(TEXT_FILES_DIR.glob("text_*.json")):
        report["text_files"]["checked"] += 1
        group_id = _extract_group_id(text_path, "text_")
        if group_id is None:
            report["text_files"]["abnormal_names"].append(str(text_path))
        try:
            data = json.loads(text_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                report["text_files"]["invalid"].append(str(text_path))
                continue
            if any(not isinstance(item, str) for item in data):
                report["text_files"]["non_string_items"].append(str(text_path))
            if any(isinstance(item, str) and not item.strip() for item in data):
                report["text_files"]["blank_items"].append(str(text_path))
        except Exception:
            report["text_files"]["invalid"].append(str(text_path))

    for image_list_path in sorted(IMAGE_FILES_DIR.glob("images_*.json")):
        report["image_lists"]["checked"] += 1
        group_id = _extract_group_id(image_list_path, "images_")
        if group_id is None:
            report["image_lists"]["abnormal_names"].append(str(image_list_path))
        try:
            data = json.loads(image_list_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                report["image_lists"]["invalid"].append(str(image_list_path))
                continue
            if group_id is None:
                continue
            group_dir = IMAGE_FILES_DIR / f"group_{group_id}"
            for filename in data:
                if not (group_dir / str(filename)).exists():
                    report["image_lists"]["missing_refs"].append({
                        "group_id": group_id,
                        "filename": str(filename),
                    })
        except Exception:
            report["image_lists"]["invalid"].append(str(image_list_path))

    return report


def log_health_report(report) -> None:
    text_files = report["text_files"]
    image_lists = report["image_lists"]
    logger.info(
        "Poke Reply 数据健康检查完成: "
        f"文本文件 {text_files['checked']} 个, "
        f"图片列表 {image_lists['checked']} 个, "
        f"异常文本文件 {len(text_files['invalid'])} 个, "
        f"异常图片列表 {len(image_lists['invalid'])} 个, "
        f"缺失图片引用 {len(image_lists['missing_refs'])} 个"
    )
    for path in text_files["abnormal_names"]:
        logger.warning(f"Poke Reply 发现异常文本文件名: {path}")
    for path in image_lists["abnormal_names"]:
        logger.warning(f"Poke Reply 发现异常图片列表文件名: {path}")
    for path in text_files["invalid"]:
        logger.error(f"Poke Reply 文本 JSON 异常: {path}")
    for path in image_lists["invalid"]:
        logger.error(f"Poke Reply 图片列表 JSON 异常: {path}")
    for missing_ref in image_lists["missing_refs"][:20]:
        logger.warning(
            f"Poke Reply 图片引用缺失: 群 {missing_ref['group_id']} 文件 {missing_ref['filename']}"
        )


def _extract_group_id(path: Path, prefix: str):
    stem = path.stem
    if not stem.startswith(prefix):
        return None
    try:
        group_id = int(stem[len(prefix):])
    except ValueError:
        return None
    return group_id if group_id > 0 else None
