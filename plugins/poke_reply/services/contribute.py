from typing import Tuple, List, Optional
from nonebot import logger
from nonebot.exception import FinishedException

from ..models.data import data_manager
from ..services import image as image_service
from ..services import text as text_service
from ..utils.network import download_image
from ..config import get_group_image_dir, MAX_TEXT_LENGTH

async def handle_text_contribution(group_id: int, text: str) -> Tuple[bool, str]:
    """处理文本投稿"""
    if not text:
        return False, "请提供要投稿的内容，格式：@我 投稿 你的文本，或回复一条消息 @我 投稿"
    
    if len(text) > MAX_TEXT_LENGTH:
        return False, f"文本太长了，请控制在{MAX_TEXT_LENGTH}字以内喵！"

    if not data_manager.ensure_group_data_loaded(group_id):
        return False, "数据加载失败，无法投稿喵！"
    
    if not data_manager.is_text_list_valid(group_id):
        return False, "数据格式错误，无法投稿喵！"

    # 文本查重
    if text_service.similarity_checker.is_similar_to_group(group_id, text):
        return False, "投稿失败，本群已经有类似的话了喵！"

    if data_manager.add_text(group_id, text):
        text_count = data_manager.get_text_count(group_id)
        image_count = data_manager.get_image_count(group_id)
        return True, f"文本投稿成功！当前群共有{text_count}条文本和{image_count}张图片喵"
    else:
        return False, "投稿失败，请稍后重试喵"

async def handle_image_contribution(group_id: int, images: list) -> Tuple[bool, str, List[str]]:
    """
    处理图片投稿
    返回: (成功与否, 提示消息, 已保存的文件名列表)
    """
    if not data_manager.ensure_group_data_loaded(group_id):
        return False, "数据加载失败，无法投稿喵！", []

    images_to_save = []
    saved_filenames = []
    success_count = 0

    # 1. 下载和查重
    for img_type, img_data, segment in images:
        if img_type == "image":
            success, image_bytes, extension = await download_image(img_data)
            if not success:
                logger.error(f"下载图片失败: {img_data}")
                return False, "图片投稿失败，下载图片时出错喵", []

            try:
                is_duplicate, existing_name = await image_service.check_duplicate_image(group_id, image_bytes)
                if is_duplicate:
                    logger.info(f"群 {group_id} 图片投稿重复: {existing_name}")
                    return False, "投稿失败，本群已经有类似的图片了喵！", []
                
                images_to_save.append((image_bytes, extension))
            except Exception as e:
                logger.error(f"图片查重时发生错误: {e}，默认放行")
                images_to_save.append((image_bytes, extension))
                
        elif img_type == "face":
            return False, "暂不支持表情符号投稿喵！", []

    if not images_to_save:
        return False, "图片投稿失败，未找到有效图片喵", []

    # 2. 保存
    for image_bytes, extension in images_to_save:
        success_add, filename = data_manager.add_image(group_id, image_bytes, extension)
        if success_add:
            success_count += 1
            saved_filenames.append(filename)
            # 更新哈希缓存
            try:
                new_file_path = get_group_image_dir(group_id) / filename
                p_hash, f_hash = image_service.get_hashes_from_bytes(image_bytes)
                if p_hash and f_hash:
                    image_service.update_hash_cache(new_file_path, p_hash, f_hash)
            except Exception as e:
                logger.error(f"更新新图片 {filename} 的哈希缓存失败: {e}")
        else:
            logger.error(f"保存图片 {filename} 失败")

    if success_count > 0:
        text_count = data_manager.get_text_count(group_id)
        image_count = data_manager.get_image_count(group_id)
        message = f"图片投稿成功！成功上传{success_count}张图片！当前群共有{text_count}条文本和{image_count}张图片喵"
        return True, message, saved_filenames
    else:
        return False, "图片投稿失败，无法保存任何图片喵", []
