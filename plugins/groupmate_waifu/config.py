from pydantic import BaseModel, Extra

class Config(BaseModel, extra=Extra.ignore):
    waifu_cd_bye :int = 300
    waifu_save :bool = True
    waifu_reset :bool = True
    waifu_he :int = 60
    waifu_be :int = 20
    waifu_ntr :int = 50
    yinpa_he :int = 50
    yinpa_be :int = 0
    yinpa_cp :int = 80
    waifu_last_sent_time_filter :int = 604800