from nonebot import on_command, get_loaded_plugins, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from typing import Optional
from utils.logging import get_logger
from utils.onebot.media import image_segment
from core.registry import get_plugin_catalog
from core.access_state import (
    plugin_status,
    save_plugin_status,
    watermark_config,
)
from plugins.plugin_manager.render import (
    PluginStatusGroup,
    PluginStatusRow,
    render_plugin_status_image,
)

from core.access import (
    get_status_override,
    get_plugin_feature_keys,
    sync_feature_statuses,
    is_plugin_enabled,
    set_plugin_status,
    is_feature_enabled,
    set_feature_status,
)

logger = get_logger("plugin_manager")

enable_plugin = on_command("启用", permission=SUPERUSER, priority=1, block=True)

disable_plugin = on_command("禁用", permission=SUPERUSER, priority=1, block=True)

list_plugins = on_command("插件开关状态", permission=SUPERUSER, priority=1, block=True)

enable_all = on_command("启用all", permission=SUPERUSER, priority=1, block=True)

disable_all = on_command("禁用all", permission=SUPERUSER, priority=1, block=True)

enable_feature = on_command("启用功能", permission=SUPERUSER, priority=1, block=True)

disable_feature = on_command("禁用功能", permission=SUPERUSER, priority=1, block=True)


@enable_plugin.handle()
async def handle_enable(bot: Bot, event: MessageEvent):
    """启用插件"""
    if not isinstance(event, GroupMessageEvent):
        await enable_plugin.finish("请在群聊中使用此命令")

    msg = event.get_plaintext().strip()
    plugin_name = msg.replace("启用", "").strip()

    if not plugin_name:
        await enable_plugin.finish("请指定要启用的插件名称，例如：启用help")

    # 检查插件
    plugin_catalog = get_plugin_catalog()
    if plugin_name not in plugin_catalog:
        plugins = get_loaded_plugins()
        plugin_names = {p.name for p in plugins}
        if plugin_name not in plugin_names:
            await enable_plugin.finish(f"未找到插件: {plugin_name}")
            return

    group_id = str(event.group_id)
    user_id = str(event.user_id)

    if ":" in plugin_name:
        parent_name, feature_name = plugin_name.split(":", 1)
        if not is_plugin_enabled(parent_name, group_id, user_id):
            await enable_plugin.finish(
                f"插件 {parent_name} 当前为禁用状态，不能单独启用子功能 {feature_name}。"
                "请先启用主插件。"
            )
        set_feature_status(parent_name, feature_name, group_id, True)
        await enable_plugin.finish(f"已启用插件: {plugin_name}")

    set_plugin_status(plugin_name, group_id, True)  # 调用此文件中的 set_plugin_status
    await enable_plugin.finish(f"已启用插件: {plugin_name}")


@disable_plugin.handle()
async def handle_disable(bot: Bot, event: MessageEvent):
    """禁用插件"""
    if not isinstance(event, GroupMessageEvent):
        await disable_plugin.finish("请在群聊中使用此命令")

    msg = event.get_plaintext().strip()
    plugin_name = msg.replace("禁用", "").strip()

    if not plugin_name:
        await disable_plugin.finish("请指定要禁用的插件名称，例如：禁用help")

    # 检查插件
    plugin_catalog = get_plugin_catalog()
    if plugin_name not in plugin_catalog:
        plugins = get_loaded_plugins()
        plugin_names = {p.name for p in plugins}
        if plugin_name not in plugin_names:
            await disable_plugin.finish(f"未找到插件: {plugin_name}")
            return

    group_id = str(event.group_id)

    if ":" in plugin_name:
        parent_name, feature_name = plugin_name.split(":", 1)
        set_feature_status(parent_name, feature_name, group_id, False)
        await disable_plugin.finish(f"已禁用插件: {plugin_name}")

    set_plugin_status(plugin_name, group_id, False)  # 调用此文件中的 set_plugin_status
    await disable_plugin.finish(f"已禁用插件: {plugin_name}")


@list_plugins.handle()
async def handle_list(bot: Bot, event: MessageEvent):
    """显示插件列表 - 返回图片（按主插件分组，子功能合并在同一框内）"""
    if not isinstance(event, GroupMessageEvent):
        await list_plugins.finish("请在群聊中使用此命令")

    plugin_catalog = get_plugin_catalog()
    group_id = str(event.group_id)

    if not plugin_catalog:
        await list_plugins.finish("暂无已登记的可管理插件")

    # 兜底文本（渲染失败时发送）
    plugin_list_text = []
    for plugin_id, plugin_name in plugin_catalog.items():
        if ":" in plugin_id:
            parent_id, feature_name = plugin_id.split(":", 1)
            is_enabled = is_feature_enabled(parent_id, feature_name, group_id, "0")
        else:
            is_enabled = is_plugin_enabled(plugin_id, group_id, "0")

        status = "✅ 启用" if is_enabled else "❌ 禁用"
        plugin_list_text.append(f"{plugin_name} ({plugin_id}) - {status}")

    message = (
        "目前接入了管理插件的有:\n" + "\n".join(plugin_list_text)
        if plugin_list_text
        else "暂无其他插件"
    )

    # 分组：按主插件出一张卡片，子功能（xxx:yyy）归入主插件卡片
    parent_order: list[str] = []
    parent_name: dict[str, str] = {}
    children: dict[str, list[tuple[str, str]]] = {}

    for plugin_id, plugin_name in plugin_catalog.items():
        if ":" in plugin_id:
            pid = plugin_id.split(":", 1)[0]
            children.setdefault(pid, []).append((plugin_id, plugin_name))
            if pid not in parent_order:
                parent_order.append(pid)
            parent_name.setdefault(pid, pid)  # 若注册表没有主插件项，用 id 兜底显示
        else:
            pid = plugin_id
            if pid not in parent_order:
                parent_order.append(pid)
            parent_name[pid] = plugin_name

    groups: list[PluginStatusGroup] = []
    for pid in parent_order:
        p_enabled = is_plugin_enabled(pid, group_id, "0")
        p_row = PluginStatusRow(
            name=parent_name.get(pid, pid),
            plugin_id=pid,
            enabled=p_enabled,
            is_child=False,
        )

        c_rows: list[PluginStatusRow] = []
        for cid, cname in children.get(pid, []):
            _, feature_name = cid.split(":", 1)
            c_enabled = is_feature_enabled(pid, feature_name, group_id, "0")
            c_rows.append(
                PluginStatusRow(
                    name=cname,
                    plugin_id=cid,
                    enabled=c_enabled,
                    is_child=True,
                )
            )

        # 子功能按英文 id 排序
        c_rows.sort(key=lambda r: r.plugin_id.lower())

        groups.append(PluginStatusGroup(parent=p_row, children=c_rows))

    # 主插件按英文 id 排序
    groups.sort(key=lambda g: g.parent.plugin_id.lower())

    try:
        wm_text = str(watermark_config.get("text", "")).strip()
        wm_pos = (
            str(watermark_config.get("position", "bottom_right")).strip()
            or "bottom_right"
        )
        img_bytes = await render_plugin_status_image(
            groups=groups,
            group_id=group_id,
            watermark_text=wm_text,
            watermark_position=wm_pos,
        )
        await list_plugins.finish(image_segment(img_bytes))
    except FinishedException:
        # finish() 会抛 FinishedException 用于中断流程；不要当作渲染失败处理
        raise
    except Exception as e:
        logger.exception(f"渲染插件开关状态图片失败: {e}")
        await list_plugins.finish(message)


@enable_all.handle()
async def handle_enable_all(bot: Bot, event: MessageEvent):
    """启用所有插件"""
    if not isinstance(event, GroupMessageEvent):
        await enable_all.finish("请在群聊中使用此命令")

    plugin_catalog = get_plugin_catalog()
    group_id = str(event.group_id)

    if not plugin_catalog:
        await enable_all.finish("暂无已登记的可管理插件")

    enabled_count = 0
    parent_plugins = [
        plugin_id for plugin_id in plugin_catalog.keys() if ":" not in plugin_id
    ]
    for plugin_id in parent_plugins:
        set_plugin_status(plugin_id, group_id, True)
        enabled_count += 1

    await enable_all.finish(f"已启用 {enabled_count} 个主插件，子功能已同步启用")


@disable_all.handle()
async def handle_disable_all(bot: Bot, event: MessageEvent):
    """禁用所有插件"""
    if not isinstance(event, GroupMessageEvent):
        await disable_all.finish("请在群聊中使用此命令")

    plugin_catalog = get_plugin_catalog()
    group_id = str(event.group_id)

    if not plugin_catalog:
        await disable_all.finish("暂无已登记的可管理插件")

    disabled_count = 0
    parent_plugins = [
        plugin_id for plugin_id in plugin_catalog.keys() if ":" not in plugin_id
    ]
    for plugin_id in parent_plugins:
        set_plugin_status(plugin_id, group_id, False)
        disabled_count += 1

    await disable_all.finish(f"已禁用 {disabled_count} 个主插件，子功能已同步禁用")


@enable_feature.handle()
async def handle_enable_feature(bot: Bot, event: MessageEvent):
    """启用插件特定功能"""
    if not isinstance(event, GroupMessageEvent):
        await enable_feature.finish("请在群聊中使用此命令")
        return

    msg = event.get_plaintext().strip()
    parts = msg.replace("启用功能", "").strip().split()

    if len(parts) != 2:
        await enable_feature.finish("格式：启用功能 插件名 功能名")
        return

    plugin_name, feature_name = parts
    feature_key = f"{plugin_name}:{feature_name}"
    plugin_catalog = get_plugin_catalog()

    if feature_key not in plugin_catalog:
        await enable_feature.finish(f"未找到功能: {feature_key}")
        return

    group_id = str(event.group_id)
    user_id = str(event.user_id)

    if not is_plugin_enabled(plugin_name, group_id, user_id):
        await enable_feature.finish(
            f"插件 {plugin_name} 当前为禁用状态，不能单独启用子功能 {feature_name}。"
            "请先启用主插件。"
        )

    set_feature_status(plugin_name, feature_name, group_id, True)  # 本地的
    await enable_feature.finish(f"已启用插件 {plugin_name} 的 {feature_name} 功能")


@disable_feature.handle()
async def handle_disable_feature(bot: Bot, event: MessageEvent):
    """禁用插件特定功能"""
    if not isinstance(event, GroupMessageEvent):
        await disable_feature.finish("请在群聊中使用此命令")
        return

    msg = event.get_plaintext().strip()
    parts = msg.replace("禁用功能", "").strip().split()

    if len(parts) != 2:
        await disable_feature.finish("格式：禁用功能 插件名 功能名")
        return

    plugin_name, feature_name = parts
    feature_key = f"{plugin_name}:{feature_name}"
    plugin_catalog = get_plugin_catalog()

    if feature_key not in plugin_catalog:
        await disable_feature.finish(f"未找到功能: {feature_key}")
        return

    group_id = str(event.group_id)

    set_feature_status(plugin_name, feature_name, group_id, False)  # 本地的
    await disable_feature.finish(f"已禁用插件 {plugin_name} 的 {feature_name} 功能")
