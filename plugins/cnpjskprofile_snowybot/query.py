from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from .data_manager import get_sc_bind  # 从数据模块导入读取函数

# 注册命令
sc_id_cmd = on_command("scid", priority=5, block=True)


@sc_id_cmd.handle()
async def _(event: MessageEvent):
    # 1. 获取用户 QQ
    user_qq = str(event.user_id)

    # 2. 从数据模块获取绑定的 ID
    bound_id = get_sc_bind(user_qq)

    # 3. 根据结果回复
    if bound_id:
        await sc_id_cmd.finish(f"您当前绑定的ID是：{bound_id}")
    else:
        # 如果没找到绑定数据，提示去绑定
        await sc_id_cmd.finish("您尚未绑定ID，请发送“sc绑定+ID”进行绑定。")