import nonebot
from nonebot import require, get_driver
from nonebot.log import logger
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

# 声明依赖并导入 nonebot-plugin-localstore
require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

# 使用 localstore 获取插件数据目录
data_dir: Path = store.get_plugin_data_dir()
plugin_dir = Path(__file__).parent
# 文件存储目录
file_storage_dir = data_dir / "msmdata"
# 绑定数据文件路径
bind_data_file = plugin_dir / "bind.json"


# 加载绑定数据
def load_bind_data() -> Dict[str, str]:
    """加载绑定数据"""
    try:
        if bind_data_file.exists():
            with open(bind_data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            logger.warning("绑定数据文件不存在")
            return {}
    except Exception as e:
        logger.error(f"加载绑定数据失败: {e}")
        return {}


# 安全文件名函数
def make_filename_safe(filename: str) -> str:
    """确保文件名安全，移除或替换非法字符"""
    # 替换Windows文件名中不允许的字符
    unsafe_chars = r'[<>:"/\\|?*\x00-\x1f]'
    safe_name = re.sub(unsafe_chars, '_', filename)
    # 移除开头和结尾的点号和空格
    safe_name = safe_name.strip('. ')
    # 如果文件名为空，使用默认名称
    if not safe_name:
        safe_name = 'unnamed'
    # 限制文件名长度
    if len(safe_name) > 200:
        safe_name = safe_name[:200]
    return safe_name


# 重命名上传的文件
def rename_uploaded_file(original_filename: str, user_id: str) -> str:
    """
    根据绑定信息重命名文件

    Args:
        original_filename: 原始文件名
        user_id: 用户QQ号

    Returns:
        新的文件名
    """
    try:
        # 加载绑定数据
        bind_data = load_bind_data()

        # 获取用户绑定内容
        bind_content = bind_data.get(str(user_id))

        if not bind_content:
            logger.info(f"用户 {user_id} 未绑定内容，文件保持原名")
            return original_filename

        # 获取文件扩展名
        file_ext = Path(original_filename).suffix

        # 获取当前时间
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 构建新文件名
        safe_bind_content = make_filename_safe(bind_content)
        new_filename = f"{user_id}_{safe_bind_content}_{current_time}{file_ext}.bin"

        # 构建完整路径
        original_path = file_storage_dir / original_filename
        new_path = file_storage_dir / new_filename

        # 检查原文件是否存在
        if not original_path.exists():
            logger.warning(f"原文件不存在: {original_path}")
            return original_filename

        # 重命名文件
        original_path.rename(new_path)
        logger.info(f"文件重命名成功: {original_filename} -> {new_filename}")

        return new_filename

    except Exception as e:
        logger.error(f"文件重命名失败: {e}")
        return original_filename


# 批量重命名已上传的文件
def batch_rename_existing_files() -> Dict[str, str]:
    """
    批量重命名已上传的文件

    Returns:
        重命名结果字典 {原文件名: 新文件名}
    """
    rename_results = {}

    try:
        # 确保目录存在
        if not file_storage_dir.exists():
            logger.warning("文件存储目录不存在")
            return rename_results

        # 加载绑定数据
        bind_data = load_bind_data()

        # 遍历所有文件
        for file_path in file_storage_dir.iterdir():
            if file_path.is_file():
                original_filename = file_path.name

                # 尝试从文件名中提取QQ号
                # 假设文件名格式可能是: QQ号_其他内容 或 直接就是QQ号
                user_id_match = re.match(r'^(\d+)', original_filename)
                if user_id_match:
                    user_id = user_id_match.group(1)

                    # 检查是否有绑定信息
                    if user_id in bind_data:
                        # 获取绑定内容
                        bind_content = bind_data[user_id]

                        # 获取文件扩展名
                        file_ext = file_path.suffix

                        # 获取文件修改时间作为上传时间
                        mtime = file_path.stat().st_mtime
                        upload_time = datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")

                        # 构建新文件名
                        safe_bind_content = make_filename_safe(bind_content)
                        new_filename = f"{user_id}_{safe_bind_content}_{upload_time}{file_ext}"
                        new_path = file_storage_dir / new_filename

                        # 重命名文件
                        file_path.rename(new_path)
                        rename_results[original_filename] = new_filename
                        logger.info(f"批量重命名: {original_filename} -> {new_filename}")

        logger.success(f"批量重命名完成，共处理 {len(rename_results)} 个文件")

    except Exception as e:
        logger.error(f"批量重命名失败: {e}")

    return rename_results


# 插件加载成功提示
logger.success("文件重命名模块加载成功！")