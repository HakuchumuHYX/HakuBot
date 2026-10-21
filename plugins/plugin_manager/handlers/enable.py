from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from utils.json_io import load_json
from utils.logging import get_exc_desc
from utils.onebot.media import image_segment
from utils.paths import PluginPaths
from core.access import is_enabled, is_unavailable, reset_group, set_enabled
from core.cooldown import get_cooldown
from core.registry import FEATURES, FEATURES_BY_ID
from plugins.plugin_manager.render import (
    PluginStatusGroup,
    PluginStatusRow,
    render_plugin_status_image,
)

WATERMARK_CONFIG_FILE = PluginPaths("plugin_manager").data / "watermark_config.json"

enable_plugin = on_command("启用", permission=SUPERUSER, priority=5, block=True)

disable_plugin = on_command("禁用", permission=SUPERUSER, priority=5, block=True)

list_plugins = on_command("插件状态", permission=SUPERUSER, priority=5, block=True)

reset_switches = on_command("重置开关", permission=SUPERUSER, priority=5, block=True)

disable_all = on_command("禁用all", permission=SUPERUSER, priority=5, block=True)


async def group_id_of(matcher: type[Matcher], event: MessageEvent) -> str:
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("请在群聊中使用此命令")
    return str(event.group_id)


async def registered_feature(matcher: type[Matcher], args: Message, usage: str) -> str:
    feature_id = args.extract_plain_text().strip()
    if not feature_id:
        await matcher.finish(usage)
    if feature_id not in FEATURES_BY_ID:
        await matcher.finish(f"未登记此功能: {feature_id}")
    return feature_id


@enable_plugin.handle()
async def handle_enable(event: MessageEvent, args: Message = CommandArg()):
    group_id = await group_id_of(enable_plugin, event)
    feature_id = await registered_feature(
        enable_plugin, args, "用法：启用 插件名，子功能写成 插件名:功能名"
    )

    parent_id = feature_id.split(":", 1)[0]
    if parent_id != feature_id and not is_enabled(parent_id, group_id):
        await enable_plugin.finish(
            f"插件 {parent_id} 当前为禁用状态，不能单独启用子功能 {feature_id}。"
            "请先启用主插件。"
        )

    set_enabled(feature_id, group_id, True)
    await enable_plugin.finish(
        f"已启用: {FEATURES_BY_ID[feature_id].name} ({feature_id})"
    )


@disable_plugin.handle()
async def handle_disable(event: MessageEvent, args: Message = CommandArg()):
    group_id = await group_id_of(disable_plugin, event)
    feature_id = await registered_feature(
        disable_plugin, args, "用法：禁用 插件名，子功能写成 插件名:功能名"
    )
    set_enabled(feature_id, group_id, False)
    await disable_plugin.finish(
        f"已禁用: {FEATURES_BY_ID[feature_id].name} ({feature_id})"
    )


@reset_switches.handle()
async def handle_reset(event: MessageEvent):
    group_id = await group_id_of(reset_switches, event)
    reset_group(group_id)
    default_off = "、".join(item.name for item in FEATURES if not item.default)
    await reset_switches.finish(
        f"已清空本群的开关设置，各插件回到默认状态。默认关闭、需要单独启用的有：{default_off}"
    )


@disable_all.handle()
async def handle_disable_all(event: MessageEvent):
    group_id = await group_id_of(disable_all, event)
    parents = [item.id for item in FEATURES if ":" not in item.id]
    for feature_id in parents:
        set_enabled(feature_id, group_id, False)
    await disable_all.finish(f"已禁用 {len(parents)} 个主插件，子功能随之停用")


@list_plugins.handle()
async def handle_list(event: MessageEvent):
    """开关、默认值、启动失败和 CD 出一张图，子功能挂在主插件下面"""
    group_id = await group_id_of(list_plugins, event)

    groups: dict[str, PluginStatusGroup] = {}
    for item in FEATURES:
        row = PluginStatusRow(
            name=item.name,
            plugin_id=item.id,
            enabled=is_enabled(item.id, group_id),
            default_on=item.default,
            unavailable=is_unavailable(item.id),
            cooldown=get_cooldown(item.id, group_id) if item.cooldown else None,
        )
        if ":" in item.id:
            groups[item.id.split(":", 1)[0]].children.append(row)
        else:
            groups[item.id] = PluginStatusGroup(parent=row, children=[])

    ordered = sorted(groups.values(), key=lambda g: g.parent.plugin_id.lower())
    for group in ordered:
        group.children.sort(key=lambda r: r.plugin_id.lower())

    # 每次现读，改了水印不用重启
    result = load_json(WATERMARK_CONFIG_FILE, default={}, missing_ok=True)
    if not result.success:
        logger.warning(f"水印配置读取失败，本次不加水印: {get_exc_desc(result.error)}")
    watermark_config = result.data

    try:
        wm_text = str(watermark_config.get("text", "")).strip()
        wm_pos = (
            str(watermark_config.get("position", "bottom_right")).strip()
            or "bottom_right"
        )
        img_bytes = await render_plugin_status_image(
            groups=ordered,
            group_id=group_id,
            watermark_text=wm_text,
            watermark_position=wm_pos,
        )
        await list_plugins.finish(image_segment(img_bytes))
    except FinishedException:
        # finish() 会抛 FinishedException 用于中断流程；不要当作渲染失败处理
        raise
    except Exception as e:
        logger.opt(exception=True).warning(f"渲染插件状态图片失败: {e}")
        lines = [
            text_line(row)
            for group in ordered
            for row in (group.parent, *group.children)
        ]
        await list_plugins.finish("本群插件状态:\n" + "\n".join(lines))


def text_line(row: PluginStatusRow) -> str:
    """出图失败时的文字版，信息和图上一样"""
    if row.unavailable:
        state = "启动失败"
    else:
        state = "开启" if row.enabled else "关闭"
    parts = [f"{row.name} ({row.plugin_id}) - {state}"]
    if not row.default_on:
        parts.append("默认关")
    if row.cooldown is not None:
        parts.append(f"CD {row.cooldown}s" if row.cooldown else "CD 未设")
    return "，".join(parts)
