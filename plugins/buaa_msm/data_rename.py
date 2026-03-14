# plugins/buaa_msm/data_rename.py
"""
文件重命名模块 - 根据绑定信息重命名上传的文件
"""
import re
from pathlib import Path
from datetime import datetime
from typing import Dict

from nonebot.log import logger

from .config import plugin_config
from .bind import bind_manager

# 从配置获取路径
file_storage_dir = plugin_config.file_storage_dir


def make_filename_safe(filename: str) -> str:
    """
    确保文件名安全，移除或替换非法字符
    
    Args:
        filename: 原始文件名
    
    Returns:
        安全的文件名
    """
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


def generate_target_filename(original_filename: str, user_id: str, *, timestamp: datetime | None = None) -> str:
    """
    生成目标文件名（不执行落盘重命名）。
    - 若用户已绑定：{user_id}_{bind_content}_{YYYYmmdd_HHMMSS}{ext}
    - 若未绑定：保持原文件名（安全化）
    """
    safe_original = make_filename_safe(original_filename)
    bind_content = bind_manager.get(str(user_id))

    if not bind_content:
        logger.info(f"用户 {user_id} 未绑定内容，文件保持原名")
        return safe_original

    file_ext = Path(safe_original).suffix or ".bin"
    dt = timestamp or datetime.now()
    current_time = dt.strftime("%Y%m%d_%H%M%S")
    safe_bind_content = make_filename_safe(bind_content)

    return f"{user_id}_{safe_bind_content}_{current_time}{file_ext}"


def rename_uploaded_file(original_filename: str, user_id: str) -> str:
    """
    根据绑定信息重命名已落盘文件（兼容旧接口）。
    """
    try:
        new_filename = generate_target_filename(original_filename, user_id)

        if new_filename == original_filename:
            return original_filename

        original_path = file_storage_dir / original_filename
        new_path = file_storage_dir / new_filename

        if not original_path.exists():
            logger.warning(f"原文件不存在: {original_path}")
            return original_filename

        original_path.rename(new_path)
        logger.info(f"文件重命名成功: {original_filename} -> {new_filename}")
        return new_filename

    except Exception as e:
        logger.error(f"文件重命名失败: {e}")
        return original_filename


def batch_rename_existing_files() -> Dict[str, str]:
    """
    批量重命名已上传的文件
    
    Returns:
        重命名结果字典 {原文件名: 新文件名}
    """
    rename_results: Dict[str, str] = {}
    
    try:
        # 确保目录存在
        if not file_storage_dir.exists():
            logger.warning("文件存储目录不存在")
            return rename_results
        
        # 遍历所有文件
        for file_path in file_storage_dir.iterdir():
            if not file_path.is_file():
                continue
            
            original_filename = file_path.name
            
            # 尝试从文件名中提取QQ号
            user_id_match = re.match(r'^(\d+)', original_filename)
            if not user_id_match:
                continue
            
            user_id = user_id_match.group(1)
            
            # 检查是否有绑定信息
            bind_content = bind_manager.get(user_id)
            if not bind_content:
                continue
            
            # 获取文件扩展名
            file_ext = file_path.suffix
            
            # 获取文件修改时间作为上传时间
            mtime = file_path.stat().st_mtime
            upload_time = datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")
            
            # 构建新文件名
            safe_bind_content = make_filename_safe(bind_content)
            new_filename = f"{user_id}_{safe_bind_content}_{upload_time}{file_ext}"
            new_path = file_storage_dir / new_filename
            
            # 避免重命名为相同的名称
            if file_path == new_path:
                continue
            
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
