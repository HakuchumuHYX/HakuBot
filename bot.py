import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter


if __name__ == "__main__":
    nonebot.init()

    driver = nonebot.get_driver()
    driver.register_adapter(ONEBOT_V11Adapter)

    nonebot.load_builtin_plugins("echo")

    # 预加载关键插件，避免 require() 时因模块已被 import 但未注册为插件而报错
    nonebot.load_plugin("nonebot_plugin_localstore")
    nonebot.load_plugin("nonebot_plugin_htmlrender")

    nonebot.load_from_toml("pyproject.toml")

    nonebot.run()
