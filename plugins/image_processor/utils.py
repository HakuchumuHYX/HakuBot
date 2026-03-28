from PIL import Image

def fix_frame_for_gif(im: Image.Image) -> Image.Image:
    """
    修复 Pillow 保存带透明度的 RGBA GIF 时出现 `ValueError: invalid palette size` 的 BUG。
    通过将 RGBA 帧预先转换为带 RGB palette 的 P 模式来避免此问题。
    
    Args:
        im: 待处理的 PIL.Image.Image 对象
        
    Returns:
        处理后的 PIL.Image.Image 对象
    """
    if im.mode == "RGBA":
        has_transparency = "transparency" in im.info
        transparency_val = im.info.get("transparency", None)
        
        im = im.convert("P")
        
        # 恢复 transparency info，因为 convert 可能会丢弃
        if has_transparency and transparency_val is not None:
             im.info["transparency"] = transparency_val
             
    if im.mode == "P" and getattr(im.palette, "mode", None) == "RGBA":
        rgb_palette = im.getpalette()
        im.putpalette(rgb_palette)
        
    return im
