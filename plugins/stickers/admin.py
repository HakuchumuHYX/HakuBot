"""超管命令：清除重复、重载stickers、删除、新建gallery、添加/删除别名"""

import asyncio
import re

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import ArgPlainText, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State

from plugins.stickers import render, store
from utils.images.dedupe import find_duplicate_pairs
from utils.logging import get_exc_desc
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary

MAX_DELETE = 5
FOLDER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_一-龥-]+$")


def _admin_command(name: str):
    return on_command(
        name, rule=command_boundary, permission=SUPERUSER, priority=5, block=True
    )


clean_matcher = _admin_command("清除重复")
reload_matcher = _admin_command("重载stickers")
delete_matcher = _admin_command("删除")
new_folder_matcher = _admin_command("新建gallery")
add_alias_matcher = _admin_command("添加别名")
remove_alias_matcher = _admin_command("删除别名")


# --- 清除重复：先发预览，发起人回「确认」才动文件 ---


@clean_matcher.handle()
async def handle_clean(state: T_State):
    duplicates = {}
    for folder in store.folders:
        pairs = await asyncio.to_thread(find_duplicate_pairs, store.images(folder))
        if pairs:
            duplicates[folder] = pairs
    if not duplicates:
        await clean_matcher.finish("未检测到重复图片")

    state["duplicates"] = duplicates
    total = sum(len(pairs) for pairs in duplicates.values())
    message = Message(f"检测到 {total} 组重复图片")
    preview = await render.cleanup_preview(duplicates)
    if preview:
        message += image_segment(preview)
    message += "\n回复「确认」执行清理，回复其他内容取消"
    await clean_matcher.send(message)


# 会话等待只认发起人在同一个群里的下一句话，超时（session_expire_timeout）后自动作废
@clean_matcher.got("answer")
async def handle_clean_answer(state: T_State, answer: str = ArgPlainText()):
    if answer.strip() not in ("确认", "确认清理"):
        await clean_matcher.finish("已取消清理")

    paths = [dup for pairs in state["duplicates"].values() for _, dup in pairs]
    moved = store.move_to_backup(paths)
    message = f"清理完成：已把 {moved} 张图片移到备份文件夹"
    if moved:
        try:
            count = await asyncio.to_thread(store.renumber)
        except Exception as e:
            logger.exception("表情包库重新编号失败")
            await clean_matcher.finish(f"{message}\n重新编号失败：{get_exc_desc(e)}")
        message += f"\n已将 {count} 张图片重新编号（1 至 {count}）"
    await clean_matcher.finish(message)


# --- 图库管理 ---


@reload_matcher.handle()
async def handle_reload():
    before = (len(store.folders), len(store.aliases))
    try:
        store.load()
    except Exception as e:
        logger.exception("重载 list.json 失败")
        await reload_matcher.finish(f"重载失败，仍使用旧数据：{get_exc_desc(e)}")
    await reload_matcher.finish(
        f"重载成功！\n之前: {before[0]}个文件夹, {before[1]}个别名"
        f"\n现在: {len(store.folders)}个文件夹, {len(store.aliases)}个别名"
    )


@delete_matcher.handle()
async def handle_delete(args: Message = CommandArg()):
    tokens = args.extract_plain_text().split()
    if not tokens:
        await delete_matcher.finish("用法：删除 编号 [编号...]")
    if len(tokens) > MAX_DELETE:
        await delete_matcher.finish(
            f"为了防止误操作，一次最多只能删除 {MAX_DELETE} 张图片。"
        )

    results = []
    for token in tokens:
        path = store.find_by_id(int(token)) if token.isdigit() else None
        if path is None:
            results.append(f"⚠️ 找不到编号 {token}")
            continue
        path.unlink()
        results.append(f"✅ 已删除 {token} (原位于 '{path.parent.name}')")
    await delete_matcher.finish("\n".join(results))


@new_folder_matcher.handle()
async def handle_new_folder(args: Message = CommandArg()):
    name = args.extract_plain_text().strip()
    if not name or len(name.split()) > 1:
        await new_folder_matcher.finish("用法：新建gallery 文件夹名")
    if not FOLDER_NAME_PATTERN.match(name):
        await new_folder_matcher.finish(f"新建失败！名称 '{name}' 包含无效字符。")
    if name.lower() in store.ALL_KEYWORDS | {"force"}:
        await new_folder_matcher.finish(
            f"新建失败！'{name}' 是保留关键字，请换个名称。"
        )
    if len(name) > 50:
        await new_folder_matcher.finish("新建失败！名称太长了。")
    if name in store.folders:
        await new_folder_matcher.finish(f"新建失败！文件夹 '{name}' 已经存在。")
    if name in store.aliases:
        await new_folder_matcher.finish(
            f"新建失败！'{name}' 已经是 '{store.aliases[name]}' 的别名了。"
        )

    store.add_folder(name)
    await new_folder_matcher.finish(f"成功新建 gallery '{name}'！现在可以向它投稿了。")


def _describe(name: str, folder: str) -> str:
    return f"'{folder}'" if name == folder else f"'{name}' (实际文件夹: '{folder}')"


@add_alias_matcher.handle()
async def handle_add_alias(args: Message = CommandArg()):
    match = re.match(r"^(\S+)\s+to\s+(\S+)$", args.extract_plain_text().strip(), re.I)
    if not match:
        await add_alias_matcher.finish("用法：添加别名 别名 to 文件夹")
    alias, name = match.groups()
    folder = store.resolve(name)
    if folder is None:
        await add_alias_matcher.finish(f"添加别名失败！文件夹 '{name}' 不存在")
    if alias in store.aliases:
        await add_alias_matcher.finish(
            f"添加失败，'{alias}' 已经是 '{store.aliases[alias]}' 的别名了"
        )
    if alias in store.folders:
        await add_alias_matcher.finish(f"添加失败，'{alias}' 已经是一个实际文件夹名称")

    store.add_alias(alias, folder)
    await add_alias_matcher.finish(
        f"已将 '{alias}' 设置为 {_describe(name, folder)} 的别名"
    )


@remove_alias_matcher.handle()
async def handle_remove_alias(args: Message = CommandArg()):
    match = re.match(r"^(\S+)\s+from\s+(\S+)$", args.extract_plain_text().strip(), re.I)
    if not match:
        await remove_alias_matcher.finish("用法：删除别名 别名 from 文件夹")
    alias, name = match.groups()
    folder = store.resolve(name)
    if folder is None:
        await remove_alias_matcher.finish(f"删除别名失败！文件夹 '{name}' 不存在")
    if alias not in store.aliases:
        await remove_alias_matcher.finish(
            f"删除别名失败！'{alias}' 不是任何文件夹的别名"
        )
    if store.aliases[alias] != folder:
        await remove_alias_matcher.finish(
            f"删除别名失败！'{alias}' 是 '{store.aliases[alias]}' 的别名，不是 '{name}' 的"
        )

    store.remove_alias(alias)
    await remove_alias_matcher.finish(
        f"已从 {_describe(name, folder)} 删除别名 '{alias}'"
    )
