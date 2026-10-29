"""QQ 命令：绑定、上传数据包、生成分析图，以及管理员命令和定时清理"""

import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from nonebot import logger, on_command, on_message, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import is_type

from utils.network.http import download_to_file
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary
from plugins.buaa_msm import msr, store
from plugins.buaa_msm.config import DATA_DIR, REFRESH_HOURS, with_site_link
from plugins.buaa_msm.gamedata import MsmError

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

UPLOAD_WAIT_SECONDS = 600
# QQ 跑在容器里，私聊文件给的是容器内路径，映射到宿主机上的挂载目录
QQ_CONTAINER_DIR = "/app/.config/QQ"
QQ_HOST_DIR = "/opt/SnowLuma/qq-config/QQ"

# 同一命令只注册一个 matcher，私聊业务 handler 和群聊提示 handler 都挂在它上面：
# NoneBot 会跳过事件类型与参数注解不匹配的 handler。注册两个同名 matcher 会报
# Duplicated prefix rule 警告。


def _private_only(matcher: type[Matcher]) -> None:
    """给 matcher 挂上群聊分支：提示只能私聊"""

    async def handle_group(event: GroupMessageEvent):
        await matcher.finish(with_site_link("该指令仅在私聊中可用"))

    matcher.handle()(handle_group)


async def _send_msr(bot: Bot, matcher: type[Matcher], qq: str) -> None:
    """生成两张图并私聊发出；数据有问题时抛 MsmError"""
    await matcher.send("正在生成统计图与位置图...")
    images = await msr.generate(qq)

    sent_any = False
    for image, label in zip(images, ("统计图", "位置图")):
        if image is None:
            await matcher.send(f"{label}生成失败，已跳过。")
            continue
        # 群里上传的文件也只私聊发图
        try:
            await bot.send_private_msg(user_id=int(qq), message=image_segment(image))
            sent_any = True
        except Exception:
            logger.opt(exception=True).warning(f"MSR {label}发送失败")
            await matcher.send(f"{label}发送失败，已跳过。")
    await matcher.send(
        with_site_link("分析结果发送完毕。") if sent_any else "抱歉，无法生成任何结果。"
    )


# ============== 绑定 ==============

bind_cmd = on_command("buaa绑定", rule=command_boundary, priority=5, block=True)


@bind_cmd.handle()
async def handle_bind(event: PrivateMessageEvent, args: Message = CommandArg()):
    content = args.extract_plain_text().strip()
    if not content:
        await bind_cmd.finish("请提供要绑定的内容，格式：buaa绑定+xxxx")

    qq = str(event.user_id)
    old = store.get_bind(qq)
    store.set_bind(qq, content)
    if old:
        await bind_cmd.finish(
            with_site_link(
                f"绑定成功！\n已更新绑定内容：\n原内容：{old}\n新内容：{content}"
            )
        )
    await bind_cmd.finish(with_site_link(f"绑定成功！\nQQ：{qq}\n绑定内容：{content}"))


query_bind = on_command("buaa查询绑定", rule=command_boundary, priority=5, block=True)


@query_bind.handle()
async def handle_query(event: PrivateMessageEvent):
    qq = str(event.user_id)
    content = store.get_bind(qq)
    if not content:
        await query_bind.finish("您尚未绑定任何内容")
    await query_bind.finish(
        with_site_link(f"您的绑定信息：\nQQ：{qq}\n绑定内容：{content}")
    )


unbind_cmd = on_command("buaa解除绑定", rule=command_boundary, priority=5, block=True)


@unbind_cmd.handle()
async def handle_unbind(event: PrivateMessageEvent):
    old = store.delete_bind(str(event.user_id))
    if old is None:
        await unbind_cmd.finish("您尚未绑定任何内容，无需解除")
    await unbind_cmd.finish(with_site_link(f"已解除绑定\n原绑定内容：{old}"))


view_all_binds = on_command(
    "buaa查看所有绑定", permission=SUPERUSER, priority=5, block=True
)


@view_all_binds.handle()
async def handle_view_all(bot: Bot, event: PrivateMessageEvent):
    binds = store.all_binds()
    if not binds:
        await view_all_binds.finish("当前没有任何绑定记录")

    result = "当前所有绑定记录：\n" + "\n".join(
        f"QQ：{qq} -> 内容：{content}" for qq, content in binds.items()
    )
    if len(result) <= 500:
        await view_all_binds.finish(result)
    for i in range(0, len(result), 500):
        await bot.send_private_msg(user_id=event.user_id, message=result[i : i + 500])
    await view_all_binds.finish("以上是所有绑定记录")


for _matcher in (bind_cmd, query_bind, unbind_cmd, view_all_binds):
    _private_only(_matcher)


# ============== 生成分析图 ==============

msr_cmd = on_command("buaamsr", rule=command_boundary, priority=5, block=True)


@msr_cmd.handle()
async def handle_msr(bot: Bot, event: PrivateMessageEvent):
    qq = str(event.user_id)
    with store.exclusive(qq) as acquired:
        if not acquired:
            await msr_cmd.finish("您的请求正在处理中，请稍候...")
        await msr_cmd.send("正在生成分析结果，请稍候...")
        try:
            await _send_msr(bot, msr_cmd, qq)
        except MsmError as e:
            logger.info(f"buaamsr 无法读取用户数据: {e}")
            await msr_cmd.finish(f"处理失败: {e}")
        except Exception as e:
            logger.exception("处理 buaamsr 命令失败")
            await msr_cmd.finish(f"处理失败: {e}")


_private_only(msr_cmd)


# ============== 上传 ==============

# 执行过「buaa上传文件」、等着发文件的人：{qq: 进入等待的时间}
_waiting: dict[str, float] = {}


def _prune_waiting() -> None:
    now = time.time()
    for qq in [qq for qq, at in _waiting.items() if now - at > UPLOAD_WAIT_SECONDS]:
        del _waiting[qq]


upload_cmd = on_command("buaa上传文件", rule=command_boundary, priority=5, block=True)


@upload_cmd.handle()
async def handle_upload(event: PrivateMessageEvent):
    _prune_waiting()
    _waiting[str(event.user_id)] = time.time()
    await upload_cmd.finish(
        with_site_link(
            "已准备好接收文件，请直接发送您要上传的文件。如需取消，请发送'取消'。"
        )
    )


_private_only(upload_cmd)


async def _is_waiting_for_file(event: PrivateMessageEvent | GroupMessageEvent) -> bool:
    _prune_waiting()
    return str(event.user_id) in _waiting


# 「取消」由下面优先级更高的 cancel_cmd 处理，到不了这里
file_handler = on_message(rule=_is_waiting_for_file, priority=10, block=False)


async def _receive_file(
    bot: Bot, event: PrivateMessageEvent | GroupMessageEvent, data: dict
) -> Path | None:
    """把 QQ 文件存进用户目录；拿不到下载地址时返回 None"""
    qq = str(event.user_id)
    file_id = data.get("file_id", "")
    logger.debug(
        f"收到文件: {data.get('file')}, ID: {file_id}, 大小: {data.get('file_size')} 字节"
    )
    # get_file 只解析图片/语音缓存；普通文件按会话类型取下载链接
    if isinstance(event, GroupMessageEvent):
        result = await bot.call_api(
            "get_group_file_url", group_id=event.group_id, file_id=file_id
        )
    else:
        result = await bot.call_api(
            "get_private_file_url",
            user_id=event.user_id,
            file_id=file_id,
            file_hash=str(data.get("file_hash") or ""),
        )
    url = result.get("url", "") if isinstance(result, dict) else result
    if not url:
        return None

    name = data.get("file", "")
    if not name or name == "unknown":
        name = f"file_{qq}_{int(event.time)}.bin"
    path = store.reserve_upload_path(qq, name)
    if url.startswith(("http://", "https://")):
        await download_to_file(url, path)
    else:
        source = unquote(url)
        if source.startswith(QQ_CONTAINER_DIR):
            source = QQ_HOST_DIR + source[len(QQ_CONTAINER_DIR) :]
        if not Path(source).exists():
            raise FileNotFoundError(f"源文件不存在: {source}")
        shutil.copy2(source, path)
    logger.info(f"用户 {qq} 上传文件: {path.name}，保存至 {path}")
    return path


@file_handler.handle()
async def handle_file(bot: Bot, event: PrivateMessageEvent | GroupMessageEvent):
    qq = str(event.user_id)
    segments = [seg for seg in event.message if seg.type == "file"]
    if not segments:
        # 等待态的提示只回私聊，避免在群里刷屏
        if isinstance(event, PrivateMessageEvent):
            await file_handler.send(
                "未检测到文件，请发送文件或输入'取消'退出上传模式。"
            )
        return

    with store.exclusive(qq) as acquired:
        if not acquired:
            await file_handler.send("您有其他请求正在处理中，请稍后再发送文件。")
            return

        try:
            path = await _receive_file(bot, event, segments[0].data)
            if path is None:
                await file_handler.send("无法获取文件路径，请重试。")
                return
            _waiting.pop(qq, None)
            await file_handler.send(
                f"文件上传成功！\n文件名：{path.name}\n正在进行预解密，请稍候..."
            )
            await msr.ingest(qq, path)
        except MsmError:
            await file_handler.send(
                "文件上传成功，但预解密失败！\n请检查文件是否为正确的 .bin 文件，然后重新上传。"
            )
            return
        except Exception as e:
            logger.exception("文件处理失败")
            _waiting.pop(qq, None)
            await file_handler.send(f"文件上传失败：{e}")
            return

        await file_handler.send(
            f"文件预解密成功！\n保存位置：{path.relative_to(DATA_DIR)}\n正在自动生成分析结果..."
        )
        retry_hint = "您可以稍后手动使用 'buaamsr' 重新生成分析结果。"
        try:
            await _send_msr(bot, file_handler, qq)
        except MsmError as e:
            logger.info(f"自动执行 MSR 分析时无法读取用户数据: {e}")
            await file_handler.send(f"自动分析失败: {e}\n{retry_hint}")
        except Exception as e:
            logger.exception("自动执行 MSR 分析失败")
            await file_handler.send(f"自动分析失败: {e}\n{retry_hint}")


cancel_cmd = on_command(
    "取消",
    rule=is_type(PrivateMessageEvent) & command_boundary,
    priority=5,
    block=True,
)


@cancel_cmd.handle()
async def handle_cancel(event: PrivateMessageEvent):
    # 不在等待状态时不回复：「取消」是常用词
    if _waiting.pop(str(event.user_id), None) is not None:
        await cancel_cmd.finish("已取消文件上传。")


# ============== 管理员 ==============


def _cleanup(*, clear_visits: bool) -> None:
    store.clear_storage()
    if clear_visits:
        store.clear_visits()


@scheduler.scheduled_job(
    "cron", hour=",".join(map(str, REFRESH_HOURS)), minute=0, id="buaa_msm_cleanup"
)
async def scheduled_cleanup():
    """烤森刷新时清空上传数据；每天第一次刷新时连来访记录一起清"""
    hour = datetime.now().hour
    logger.debug(f"执行 {hour}:00 文件清理任务")
    _cleanup(clear_visits=hour == REFRESH_HOURS[0])


cleanup_cmd = on_command("清理文件", permission=SUPERUSER, priority=5, block=True)


@cleanup_cmd.handle()
async def handle_cleanup():
    _cleanup(clear_visits=True)
    await cleanup_cmd.finish("文件及访问历史清理完成")


def _format_size(size: int) -> str:
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size >= scale:
            return f"{size / scale:.2f} {unit}"
    return f"{size} B"


stats_cmd = on_command("文件统计", permission=SUPERUSER, priority=5, block=True)


@stats_cmd.handle()
async def handle_stats():
    uploads = store.list_uploads()
    if not uploads:
        await stats_cmd.finish("文件存储目录为空")

    per_user = Counter(path.parent.name for path in uploads)
    lines = "\n".join(f"  - {qq}: {count} 个数据包" for qq, count in per_user.items())
    await stats_cmd.finish(
        "文件统计信息:\n"
        f"用户数: {len(per_user)}，数据包: {len(uploads)} 个\n"
        f"总大小: {_format_size(store.storage_size())}\n"
        f"按用户分布:\n{lines}"
    )


list_files_cmd = on_command(
    "文件列表",
    rule=is_type(PrivateMessageEvent),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@list_files_cmd.handle()
async def handle_list_files():
    uploads = store.list_uploads()
    if not uploads:
        await list_files_cmd.finish("文件存储目录为空。")
    lines = [
        f"{i}. {path.name} ({_format_size(path.stat().st_size)})"
        for i, path in enumerate(uploads, start=1)
    ]
    await list_files_cmd.finish(
        f"已上传的文件 (共 {len(uploads)} 个):\n" + "\n".join(lines)
    )
