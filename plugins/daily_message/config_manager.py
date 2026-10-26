from core.settings import ConfigModel, load_config


class Schedule(ConfigModel):
    id: str
    hour: int
    minute: int
    message: str


class Config(ConfigModel):
    schedules: list[Schedule]


plugin_config = load_config("daily_message", Config)
