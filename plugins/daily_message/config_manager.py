from core.settings import ConfigModel, load_config


class Schedule(ConfigModel):
    id: str
    hour: int
    minute: int
    message: str


class Config(ConfigModel):
    schedules: list[Schedule]


def read_config() -> Config:
    return load_config("daily_message", Config)


# 运行中生效的定时任务配置；「重载定时任务」会整体替换
plugin_config = read_config()
