from nonebot.log import logger


def get_logger(name: str):
    """
    获取带名称的 logger，实际上是对 nonebot logger 的简单封装
    """
    return logger.bind(name=name)


def get_exc_desc(e: Exception) -> str:
    """
    获取异常的简短描述
    """
    return f"{type(e).__name__}: {str(e)}"
