from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg
from nonebot.adapters import Message
from .data_manager import save_sc_bind  # 导入刚才写的数据保存函数

# 注册命令
sc_bind = on_command("scbind", aliases={"sc绑定"}, priority=5, block=True)


@sc_bind.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    # 1. 获取输入
    target_id = args.extract_plain_text().strip()

    # 2. 校验输入
    if not target_id:
        await sc_bind.finish("绑定失败：请输入要绑定的ID。\n格式示例：sc绑定 114514")

    # 3. 获取用户信息
    user_qq = str(event.user_id)

    # 4. 调用数据模块保存
    save_sc_bind(user_qq, target_id)

    # 5. 反馈
    await sc_bind.finish(f"绑定成功！\nQQ: {user_qq}\n已绑定 ID: {target_id}")