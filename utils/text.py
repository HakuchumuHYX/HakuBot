def truncate(text: str, length: int = 50) -> str:
    """
    截断过长的字符串
    """
    if len(text) > length:
        return text[:length] + "..."
    return text
