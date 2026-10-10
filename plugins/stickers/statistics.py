# statistics.py
from plugins.stickers.send import get_folder_display_info


def get_sticker_statistics() -> str:
    """
    获取贴图文件夹统计信息

    返回: 格式化的统计信息字符串
    """
    folder_info_list = get_folder_display_info()
    if not folder_info_list:
        return "当前没有可用的贴图文件夹"

    # 按文件夹名称排序
    sorted_folders = sorted(folder_info_list, key=lambda x: x["name"])

    # 构建统计信息
    lines = ["当前stickers列表："]

    for folder_info in sorted_folders:
        folder_name = folder_info["name"]
        image_count = folder_info["image_count"]
        aliases = folder_info.get("aliases", [])

        if aliases:
            alias_text = f" (别名: {', '.join(aliases)})"
        else:
            alias_text = ""

        lines.append(f"{folder_name}{alias_text}：{image_count}张")

    # 添加总计信息
    total_folders = len(sorted_folders)
    total_images = sum(folder_info["image_count"] for folder_info in sorted_folders)
    lines.append(f"\n总计：{total_folders}个文件夹，{total_images}张图片")

    return "\n".join(lines)


def handle_statistics_command(message_text: str) -> bool:
    """
    检查消息是否为查看统计命令

    返回: 是否为统计命令
    """
    return message_text.strip() == "查看stickers"
