def get_exc_desc(e: Exception) -> str:
    """
    获取异常的简短描述
    """
    return f"{type(e).__name__}: {str(e)}"
