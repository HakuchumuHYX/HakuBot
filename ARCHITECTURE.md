# HakuBot 结构说明

- `plugins/`：插件入口、命令处理及各自的业务逻辑。
- `core/`：应用装配、插件开关、冷却、配置读取和资源生命周期。
- `utils/`：公共网络、文件、图片、OneBot 消息、渲染能力，以及多个插件共用的 B 站 Web 签名（`bilibili.py`）。
- `config/plugins/<插件名>/config.json`：插件配置，不入库；`config-example/<插件名>/config.json` 是入库的默认配置，列出全部字段，地址、密钥这类用占位值。
- `data/shared/fonts/`：公共字体 `MiSans-{Regular,Medium,Semibold,Bold,Heavy}.ttf`，每个字重必须是真实文件，否则模板里的粗体不生效。
- `data/shared/playwright/`：浏览器资源。
- `data/shared/rembg_models/`：抠图模型。
- 和项目根目录并列的外部仓库，由各自的同步脚本更新，路径统一从 `utils/paths.py` 取，不写进配置：`../haruki-sekai-master/`（PJSK masterdata，`sekai_master_dir()`）、`../moesekai-hub/`（PJSK 剧情文本，`moesekai_hub_dir()`）。

公共工具不依赖具体插件。业务数据通过 `PluginPaths` 访问。

图片渲染统一走 Chromium：每个插件把模板放在自己的 `templates/`，通过 `utils/rendering/engine.py` 的 `render_template` 截图。模板可直接使用 `font_css`（本地 MiSans 五个字重，family 为 `Haku`）和 `dark`（上海时间 18:00–06:00 为夜间）。PIL 只用于贴图合成与图片处理（buaa_msm 地图、stickers 概览拼图、B 站九宫格、ximg 拼图等）。

插件不带帮助命令，用法统一写在帮助站；也不写 `__plugin_meta__`，没有地方读它。`utils/rendering/templates/` 只保留通用的 Markdown / 纯文本转图模板。

配置一律是插件里定义的 pydantic 模型（继承 `core.settings.ConfigModel`，多余的键直接报错），用 `core.settings.load_config` 读取，不读 `.env`。模型只声明类型和约束（`Field(ge=...)` 等），不写默认值，调用点也不写 `getattr(..., 默认值)`、`or 默认值` 兜底；默认值只存在于 `config-example/`。缺文件、缺字段、写坏、校验不过时插件加载失败并报 ERROR。加字段时同时改示例和线上 `config.json`。命令改配置走 `update_fields`。

一次性下载（拿一个 URL 的字节或文件）走 `utils/network`（`download_bytes`、`download_to_file`、`fetch_image`），自带大小上限、重试和原子写入；有自己的鉴权、请求头或会话状态的 API 客户端才自建会话。

群日报和早报各用自己的 LLM 配置，不读取其他插件的配置作为回退。正常运行使用 `uv run python bot.py`。

## matcher 分层

NoneBot 同一优先级的 matcher 全部会跑，只要有一个 `block=True` 的 permission 和 rule 都通过，更低优先级就收不到这条消息；handler 里直接 `return`、或者参数类型对不上被跳过，都一样会挡住。所以按“谁先看到”分层，`priority` 和 `block` 一律显式写：

| 优先级 | 放什么 | block |
| --- | --- | --- |
| 1 | 旁听：统计、日报记录、复读、入群欢迎、戳一戳、好友请求 | 否 |
| 3 | 进行中游戏的答题监听 | 否；本会话确实在答题时才 `stop_propagation()` |
| 5 | 所有命令，包括超管命令 | 是 |
| 10 | 自由文本触发：呼叫 ATRI、二择、娶群友、表情包关键词、B 站链接、文件上传、撤回、转文字 | 是；旁路性质的（表情包关键词、文件上传、B 站解析）为否 |

- rule 只在这个 matcher 确实会处理时才通过。只处理群消息的用户命令写 `permission=GROUP`，不能只靠 handler 参数类型。
- 身份要求写在 `permission=`：超管 `SUPERUSER`，群管 `GROUP_ADMIN | GROUP_OWNER | SUPERUSER`。没权限的人不匹配、不回复，handler 里不再判断。
- `COMMAND_START` 含空字符串、命令按最长前缀匹配，用户命令都要挂 `utils.onebot.rules.command_boundary`，否则「chatgpt好用吗」会触发 chat。帮助站里写明参数连写的命令（stickers 的「查看947」「看所有miku」）改为在 rule 里检查参数本身。

## 插件开关与 CD

- 能开关的功能都登记在 `core/registry.py`，`a:b` 是 a 的子功能，ID 与 NoneBot 的插件名一致。不请自来的定时推送（日报、定时消息、早报）写 `default=False`；代码里确实检查了 CD 的功能才写 `cooldown=True`，CD 命令只接受这些。
- 开关写在 rule 里，不在 handler 里查：主动触发的 matcher 挂 `core.access.switch_rule(id)`，超管不受限制；旁听（复读、统计计数、消息记录、入群欢迎）挂 `switch_rule(id, superuser_bypass=False)`，否则关了的群照样处理超管的消息。关掉后 rule 不通过，消息继续往下传。超管命令不挂开关。
- 定时推送和数据处理用 `is_enabled(id, group_id)`，不看触发者。
- 子功能要求主插件开着；改主插件不动子功能的单独设置。启动钩子失败（`runtime.health` 为 unavailable）的功能，对谁、在私聊里也都不响应。
- CD 留在 handler：冷却中要回复剩余秒数，而且成功之后才扣。`cooldown_remaining(id, event)` 检查，成功后 `mark_cooldown(id, event)`；私聊和超管不受 CD 限制，时间戳只在内存里，重启清零。
- hltv_sub 的群开关由群管理自己开关，并决定是否启动客户端，不接入这套。

## 日志

统一 `from nonebot import logger`；`webconsole_bridge` 直接用 loguru（挂 sink、`webconsole_bridge_internal` 防回环）。

日志有两个读者：`bot.py` 的 stdout（`output.log`）里 nonebot 内核、uvicorn、websockets 只出 WARNING 起（内核收到事件那一行带消息原文，照常输出），其余来源跟 `log_level`；
webconsole bridge 自己的 sink 只收 WARNING 起，并按级别判定一次响应——出现 ERROR 记失败，出现 WARNING 留完整诊断。所以级别按这个语义用：

| 级别 | 放什么 |
| --- | --- |
| ERROR | 这件事没办成：用户没拿到结果、推送没送达、任务整个失败 |
| WARNING | 办成了但降级了，或外部依赖异常、下轮会重试 |
| INFO | 结果和状态变化：推送送达、订阅变更、定时任务结论 |
| DEBUG | 过程细节，排查时才开 |

- 同一状态只在进入和恢复时各记一次，不要每轮都报。
- 发送失败（风控 1200、超时等）按最终结果定级：下轮会重试或只是部分群没发出去记 WARNING，放弃了、彻底没送到记 ERROR。
- 意料之外的异常用 `logger.exception`；意料之中的（超时、风控 1200）一行 `warning`，用 `utils.logging.get_exc_desc(e)`。
- 已降级兜底、但原因可能是代码 bug 时，用 `logger.opt(exception=True).warning`：保留 traceback，又不被控制台记成失败。
- loguru 不认 `exc_info=True`：不会带 traceback，异常文本里有 `{}` 时日志调用本身会抛 `KeyError`。
