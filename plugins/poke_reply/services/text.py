from difflib import SequenceMatcher
from typing import Dict, List, Tuple
from nonebot import logger
from PIL import Image, ImageDraw, ImageFont

from ..config import SIMILARITY_THRESHOLD, PLUGIN_DIR
from ..models.data import data_manager
from ..utils.common import preprocess_text

# --- 文本相似度检查 ---

class SimilarityChecker:
    def __init__(self, threshold: float = SIMILARITY_THRESHOLD):
        self.threshold = threshold
        self.group_cache: Dict[int, List[str]] = {}

    def is_similar_to_group(self, group_id: int, new_text: str) -> bool:
        """检查新文本是否与指定群组的现有文本相似"""
        data_manager.ensure_group_data_loaded(group_id)
        if group_id not in data_manager.group_texts:
            return False

        existing_texts = data_manager.group_texts[group_id]
        processed_new = preprocess_text(new_text)

        for existing in existing_texts:
            processed_existing = preprocess_text(existing)
            similarity = SequenceMatcher(None, processed_new, processed_existing).ratio()
            if similarity >= self.threshold:
                return True
        return False

    def calculate_similarity(self, text1: str, text2: str) -> float:
        processed1 = preprocess_text(text1)
        processed2 = preprocess_text(text2)
        return SequenceMatcher(None, processed1, processed2).ratio()

    def clear_group_cache(self, group_id: int):
        if group_id in self.group_cache:
            del self.group_cache[group_id]

similarity_checker = SimilarityChecker()

# --- 文本转图片 ---

HTMLRENDER_AVAILABLE = False
try:
    from nonebot_plugin_htmlrender import md_to_pic
    HTMLRENDER_AVAILABLE = True
except ImportError:
    logger.warning("nonebot_plugin_htmlrender 未安装，将使用 PIL 进行简单文本转图片")

async def convert_text_to_image(text: str, group_id: int) -> Tuple[bool, bytes]:
    """将文本转换为图片"""
    if HTMLRENDER_AVAILABLE:
        try:
            # 使用 md_to_pic，因为它支持 Markdown 格式，效果较好
            # 为了防止 Markdown 注入，可以考虑是否进行转义，这里暂且假设用户输入是安全的或预处理过
            # 添加一些基本的 CSS 样式使看起来更像聊天气泡
            css = """
            <style>
                body {
                    font-family: "Microsoft YaHei", "WenQuanYi Micro Hei", sans-serif;
                    background-color: #f2f2f2;
                    padding: 20px;
                }
                .markdown-body {
                    background-color: white;
                    padding: 20px;
                    border-radius: 10px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                    font-size: 16px;
                    line-height: 1.6;
                    color: #333;
                }
            </style>
            """
            # 简单的换行处理，虽然md_to_pic会处理，但保留原格式更好
            formatted_text = text.replace("\n", "  \n")
            image_data = await md_to_pic(
                md=formatted_text + css,
                width=500,  # 限制宽度，防止过宽
            )
            return True, image_data
        except Exception as e:
            logger.error(f"htmlrender 转换文本失败: {e}，尝试使用 PIL 降级方案")
    
    # PIL 降级方案
    return await _convert_text_to_image_pil(text)

async def _convert_text_to_image_pil(text: str) -> Tuple[bool, bytes]:
    try:
        font_size = 20
        padding = 20
        line_spacing = 5
        max_width = 600
        
        # 尝试加载中文字体，如果没有则使用默认
        font_paths = [
            "msyh.ttc", "simhei.ttf", # Windows
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", # Linux
            "/System/Library/Fonts/PingFang.ttc" # macOS
        ]
        
        font = None
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, font_size)
                break
            except:
                continue
        
        if font is None:
            font = ImageFont.load_default()

        # 简单的自动换行逻辑
        lines = []
        for paragraph in text.split('\n'):
            line = ""
            for char in paragraph:
                test_line = line + char
                # getbbox returns (left, top, right, bottom)
                bbox = font.getbbox(test_line)
                width = bbox[2] - bbox[0]
                if width > max_width - 2 * padding:
                    lines.append(line)
                    line = char
                else:
                    line = test_line
            lines.append(line)
        
        # 计算图片尺寸
        text_height = sum([font.getbbox(line)[3] - font.getbbox(line)[1] + line_spacing for line in lines])
        img_width = max_width
        img_height = text_height + 2 * padding
        
        image = Image.new('RGB', (img_width, img_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(image)
        
        y_text = padding
        for line in lines:
            draw.text((padding, y_text), line, font=font, fill=(0, 0, 0))
            bbox = font.getbbox(line)
            height = bbox[3] - bbox[1]
            y_text += height + line_spacing
            
        import io
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        return True, img_byte_arr.getvalue()
        
    except Exception as e:
        logger.error(f"PIL 转换文本失败: {e}")
        return False, b""
