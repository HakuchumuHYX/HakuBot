import re
import sys

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

# ---- 已知的“重复命令注册”警告过滤 ----
# NoneBot 在同一命令词被多个 matcher 注册时会打 Duplicated prefix rule 警告。
# 以下重复是已知且有意为之，功能完全正常，予以精确过滤：
# - nonebot_bison（第三方包）：sub_manager 会把同一批命令再注册一个“无权限提示”
#   matcher（非管理员触发时回复无权限），每次启动必然告警，无法在不改动
#   site-packages 的情况下消除；
# - 「取消」：buaa_msm（私聊上传取消）与 stickers（清理确认取消）两个插件
#   各自合理地使用了同名命令，运行时按 rule/priority 正常分流。
# 未列入白名单的命令若出现重复注册，仍会正常告警。
_KNOWN_DUP_COMMANDS = frozenset({
    # nonebot_bison 内部双注册
    "添加订阅", "删除订阅", "群管理", "管理后台",
    "添加cookie", "关联cookie", "取消关联cookie", "删除cookie",
    # 跨插件同名命令（buaa_msm / stickers）
    "取消",
})

_DUP_RULE_RE = re.compile(r'^Duplicated prefix rule "\.?(.+)"$')


def _make_log_filter():
    from nonebot.log import default_filter

    def _filter(record):
        m = _DUP_RULE_RE.match(record["message"])
        if m and m.group(1) in _KNOWN_DUP_COMMANDS:
            return False
        return default_filter(record)

    return _filter


def _install_log_filter():
    from loguru import logger
    from nonebot.log import default_format, logger_id

    logger.remove(logger_id)
    logger.add(
        sys.stdout,
        level=0,
        diagnose=False,
        filter=_make_log_filter(),
        format=default_format,
    )


if __name__ == "__main__":
    nonebot.init()
    _install_log_filter()

    driver = nonebot.get_driver()
    driver.register_adapter(ONEBOT_V11Adapter)

    nonebot.load_builtin_plugins("echo")

    # 预加载关键插件，避免 require() 时因模块已被 import 但未注册为插件而报错
    nonebot.load_plugin("nonebot_plugin_localstore")
    nonebot.load_plugin("nonebot_plugin_htmlrender")

    nonebot.load_from_toml("pyproject.toml")

    nonebot.run()
