from typing import Dict, List
from .send import sticker_folders, count_images_in_folder


def get_sticker_statistics() -> str:
    """
    获取贴图文件夹统计信息

    返回: 格式化的统计信息字符串
    """
    if not sticker_folders:
        return "当前没有可用的贴图文件夹"

    # 获取所有文件夹并按名称排序
    sorted_folders = sorted(sticker_folders.keys())

    # 构建统计信息
    lines = ["当前stickers列表："]

    for folder_name in sorted_folders:
        image_count = count_images_in_folder(folder_name)
        lines.append(f"{folder_name}：{image_count}张")

    # 添加总计信息
    total_folders = len(sticker_folders)
    total_images = sum(count_images_in_folder(folder) for folder in sticker_folders)
    lines.append(f"\n总计：{total_folders}个文件夹，{total_images}张图片")

    return "\n".join(lines)


def handle_statistics_command(message_text: str) -> bool:
    """
    检查消息是否为查看统计命令

    返回: 是否为统计命令
    """
    return message_text.strip() == "查看stickers"