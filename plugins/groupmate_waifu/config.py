from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths


class Config(ConfigModel):
    # 指定娶的点数（1~100）：不超过 waifu_he 娶到，再往上 waifu_be 个点变单身，其余没娶到
    waifu_he: int
    waifu_be: int
    # 指定的人已有 CP 时抢过来的概率
    waifu_ntr: int
    # 指定透群友的成功概率；对象是自己 CP 时用 yinpa_cp
    yinpa_he: int
    yinpa_cp: int
    # 卡池只算这么多秒内发过言的群友
    waifu_last_sent_time_filter: int


config = load_config("groupmate_waifu", Config)

DATA_DIR = PluginPaths("groupmate_waifu").data
DAILY_FILE = DATA_DIR / "daily.json"
PROTECT_FILE = DATA_DIR / "list_protect.json"
