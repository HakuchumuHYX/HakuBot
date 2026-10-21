from core.settings import ConfigModel


class HelpConfig(ConfigModel):
    # 回复给用户的整段文字，帮助站网址写在这里，不进代码
    text: str
