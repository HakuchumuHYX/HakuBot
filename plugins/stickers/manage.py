# manage.py
import json
import re
from pathlib import Path
from typing import Tuple, Optional, Dict, List
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot import get_driver

# vvvvvv 【修改：导入了 sticker_dir】 vvvvvv
from .send import sticker_dir, list_json_path, load_sticker_list, folder_configs, alias_to_folder, sticker_folders, \
    resolve_folder_name


# ^^^^^^ 【修改：导入了 sticker_dir】 ^^^^^^


def is_superuser(user_id: str) -> bool:
    """
    检查用户是否为超级用户

    返回: 是否为超级用户
    """
    try:
        # 从 NoneBot 配置中获取超级用户列表
        superusers = get_driver().config.superusers
        if superusers and user_id in superusers:
            return True
    except:
        pass

    return False


async def handle_manage_command(message_text: str, event: GroupMessageEvent) -> Optional[str]:
    """
    处理管理命令

    返回: 回复消息，如果不是管理命令则返回None
    """
    message_text = message_text.strip()

    # 重载列表命令 (su only)
    if message_text == "重载stickers":
        if not is_superuser(str(event.user_id)):
            return "权限不足，只有超级用户才能重载列表"

        return reload_sticker_list()

    # vvvvvv 【新增：新建Gallery命令】 vvvvvv
    new_gallery_match = re.match(r'^sticker 新建gallery\s+(\S+)$', message_text, re.IGNORECASE)
    if new_gallery_match:
        if not is_superuser(str(event.user_id)):
            return "权限不足，只有超级用户才能新建gallery"

        gallery_name = new_gallery_match.group(1).strip()
        # 调用新的处理函数
        return create_new_gallery(gallery_name)
    # ^^^^^^ 【新增：新建Gallery命令】 ^^^^^^

    # 添加别名命令 (所有用户)
    add_alias_match = re.match(r'^添加别名\s+(\S+)\s+to\s+(\S+)$', message_text, re.IGNORECASE)
    if add_alias_match:
        alias = add_alias_match.group(1).strip()
        folder_name = add_alias_match.group(2).strip()
        return await add_alias(alias, folder_name, str(event.user_id))

    # 删除别名命令 (su only)
    remove_alias_match = re.match(r'^删除别名\s+(\S+)\s+from\s+(\S+)$', message_text, re.IGNORECASE)
    if remove_alias_match:
        if not is_superuser(str(event.user_id)):
            return "权限不足，只有超级用户才能删除别名"

        alias = remove_alias_match.group(1).strip()
        folder_name = remove_alias_match.group(2).strip()
        return remove_alias(alias, folder_name)

    return None


def reload_sticker_list() -> str:
    """
    重载 list.json 配置

    返回: 操作结果消息
    """
    try:
        old_count = len(folder_configs)
        old_alias_count = len(alias_to_folder)
        load_sticker_list()
        new_count = len(folder_configs)
        new_alias_count = len(alias_to_folder)

        return f"重载成功！\n之前: {old_count}个文件夹, {old_alias_count}个别名\n现在: {new_count}个文件夹, {new_alias_count}个别名"
    except Exception as e:
        return f"重载失败: {e}"


# vvvvvv 【新增：创建Gallery的函数】 vvvvvv
def create_new_gallery(gallery_name: str) -> str:
    """
    创建一个新的 gallery (文件夹和配置)

    返回: 操作结果消息
    """
    try:
        # 检查名称有效性 (不允许特殊字符，防止路径遍历等)
        if not re.match(r'^[a-zA-Z0-9_\u4e00-\u9fa5-]+$', gallery_name):
            return f"新建失败！名称 '{gallery_name}' 包含无效字符。"
        if gallery_name.lower() in ["stickers", "sticker", "表情", "表情包", "force"]:
            return f"新建失败！'{gallery_name}' 是保留关键字，请换个名称。"
        if len(gallery_name) > 50:
            return "新建失败！名称太长了。"

        # 检查文件夹是否已存在 (使用 sticker_folders 检查)
        if gallery_name in sticker_folders:
            return f"新建失败！文件夹 '{gallery_name}' 已经存在。"

        # 检查别名是否已被使用
        if gallery_name in alias_to_folder:
            current_folder = alias_to_folder[gallery_name]
            return f"新建失败！'{gallery_name}' 已经是 '{current_folder}' 的别名了。"

        # --- 1. 创建物理文件夹 ---
        new_folder_path = sticker_dir / gallery_name
        new_folder_path.mkdir(exist_ok=True)
        print(f"已创建新文件夹: {new_folder_path}")

        # --- 2. 更新 list.json ---
        if not list_json_path.exists():
            # 如果 list.json 不存在 (例如首次运行)，创建它
            data = {"folders": []}
        else:
            try:
                with open(list_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if "folders" not in data or not isinstance(data["folders"], list):
                    data["folders"] = []
            except json.JSONDecodeError:
                # 文件损坏，进行备份和重置
                list_json_path.rename(list_json_path.with_suffix('.json.bak'))
                data = {"folders": []}
                print("警告: list.json 文件损坏，已备份并重置。")

        # 添加新配置
        new_config = {
            "name": gallery_name,
            "aliases": []
        }
        data["folders"].append(new_config)

        # 保存更新后的 list.json
        with open(list_json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # --- 3. 重载配置到内存 ---
        load_sticker_list()

        print(f"超级用户 新建了 gallery '{gallery_name}'")
        return f"成功新建 gallery '{gallery_name}'！现在可以向它投稿了。"

    except Exception as e:
        return f"新建 gallery 失败: {e}"


# ^^^^^^ 【新增：创建Gallery的函数】 ^^^^^^


async def add_alias(alias: str, folder_name: str, user_id: str) -> str:
    """
    为文件夹添加别名

    返回: 操作结果消息
    """
    try:
        # 解析实际文件夹名称（支持别名）
        actual_folder_name = resolve_folder_name(folder_name)

        # 检查文件夹是否存在
        if actual_folder_name not in sticker_folders:
            return f"添加别名失败！文件夹 '{folder_name}' 不存在"

        # 检查别名是否已被使用
        if alias in alias_to_folder:
            current_folder = alias_to_folder[alias]
            return f"添加失败，'{alias}' 已经是 '{current_folder}' 的别名了"

        # 检查别名是否是其他文件夹的实际名称
        if alias in sticker_folders:
            return f"添加失败，'{alias}' 已经是一个实际文件夹名称"

        # 加载当前的 list.json
        if not list_json_path.exists():
            return "添加别名失败！list.json 文件不存在"

        with open(list_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 找到对应的文件夹配置并添加别名
        folder_found = False
        for folder_config in data["folders"]:
            if folder_config["name"] == actual_folder_name:
                if "aliases" not in folder_config:
                    folder_config["aliases"] = []

                # 检查别名是否已存在（避免重复）
                if alias not in folder_config["aliases"]:
                    folder_config["aliases"].append(alias)

                folder_found = True
                break

        if not folder_found:
            return f"添加别名失败！在 list.json 中未找到文件夹 '{actual_folder_name}'"

        # 保存更新后的 list.json
        with open(list_json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 重载配置
        load_sticker_list()

        # 记录操作日志
        user_info = "超级用户" if is_superuser(user_id) else f"用户{user_id}"
        print(f"{user_info} 为文件夹 '{actual_folder_name}' 添加了别名 '{alias}'")

        # 显示信息
        display_info = f"'{actual_folder_name}'"
        if folder_name != actual_folder_name:
            display_info = f"'{folder_name}' (实际文件夹: '{actual_folder_name}')"

        return f"已将 '{alias}' 设置为 {display_info} 的别名"

    except Exception as e:
        return f"添加别名失败: {e}"


def remove_alias(alias: str, folder_name: str) -> str:
    """
    从文件夹删除别名

    返回: 操作结果消息
    """
    try:
        # 解析实际文件夹名称（支持别名）
        actual_folder_name = resolve_folder_name(folder_name)

        # 检查文件夹是否存在
        if actual_folder_name not in sticker_folders:
            return f"删除别名失败！文件夹 '{folder_name}' 不存在"

        # 检查别名是否存在
        if alias not in alias_to_folder:
            return f"删除别名失败！'{alias}' 不是任何文件夹的别名"

        # 检查别名是否属于指定的文件夹
        if alias_to_folder[alias] != actual_folder_name:
            actual_folder = alias_to_folder[alias]
            return f"删除别名失败！'{alias}' 是 '{actual_folder}' 的别名，不是 '{folder_name}' 的"

        # 加载当前的 list.json
        if not list_json_path.exists():
            return "删除别名失败！list.json 文件不存在"

        with open(list_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 找到对应的文件夹配置并删除别名
        folder_found = False
        alias_removed = False

        for folder_config in data["folders"]:
            if folder_config["name"] == actual_folder_name:
                if "aliases" in folder_config and alias in folder_config["aliases"]:
                    folder_config["aliases"].remove(alias)
                    alias_removed = True

                folder_found = True
                break

        if not folder_found:
            return f"删除别名失败！在 list.json 中未找到文件夹 '{actual_folder_name}'"

        if not alias_removed:
            return f"删除别名失败！在 '{actual_folder_name}' 的别名列表中未找到 '{alias}'"

        # 保存更新后的 list.json
        with open(list_json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 重载配置
        load_sticker_list()

        print(f"超级用户 从文件夹 '{actual_folder_name}' 删除了别名 '{alias}'")

        # 显示信息
        display_info = f"'{actual_folder_name}'"
        if folder_name != actual_folder_name:
            display_info = f"'{folder_name}' (实际文件夹: '{actual_folder_name}')"

        return f"已从 {display_info} 删除别名 '{alias}'"

    except Exception as e:
        return f"删除别名失败: {e}"


def get_folder_aliases(folder_name: str) -> List[str]:
    """
    获取指定文件夹的所有别名

    返回: 别名列表
    """
    for folder_config in folder_configs:
        if folder_config["name"] == folder_name:
            return folder_config.get("aliases", [])
    return []


def get_all_aliases_info() -> str:
    """
    获取所有别名的信息（用于调试）

    返回: 格式化的别名信息
    """
    if not folder_configs:
        return "当前没有配置任何文件夹"

    lines = ["当前别名配置："]

    for folder_config in folder_configs:
        folder_name = folder_config["name"]
        aliases = folder_config.get("aliases", [])

        if aliases:
            lines.append(f"{folder_name}: {', '.join(aliases)}")
        else:
            lines.append(f"{folder_name}: 无别名")

    return "\n".join(lines)