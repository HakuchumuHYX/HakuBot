# HakuBot 结构说明

- `plugins/`：插件入口、命令处理及各自的业务逻辑。
- `core/`：应用装配、插件开关、冷却、配置访问和资源生命周期。
- `utils/`：公共网络、文件、图片、OneBot 消息及渲染能力。
- `config/plugins/<插件名>/`：插件配置，文件内容保持原样。
- `data/shared/fonts/`：公共字体 `MiSans-{Regular,Medium,Semibold,Bold,Heavy}.ttf`，每个字重必须是真实文件，否则模板里的粗体不生效。
- `data/shared/playwright/`：浏览器资源。
- `data/shared/rembg_models/`：抠图模型。

公共工具不依赖具体插件。业务数据通过 `PluginPaths` 访问，现有记录和外部路径保持有效；不需要额外的迁移或验收流程。

图片渲染统一走 Chromium：每个插件把模板放在自己的 `templates/`，通过 `utils/rendering/engine.py` 的 `render_template` 截图。模板可直接使用 `font_css`（本地 MiSans 五个字重，family 为 `Haku`）和 `dark`（上海时间 18:00–06:00 为夜间）。PIL 只用于贴图合成与图片处理（buaa_msm 地图、stickers 概览拼图、B 站九宫格、ximg 拼图等）。

插件不带帮助命令，用法统一写在帮助站。`utils/rendering/templates/` 只保留通用的 Markdown / 纯文本转图模板。

群日报和早报各用自己的 LLM 配置，不读取其他插件的配置作为回退。正常运行使用 `uv run python bot.py`。

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
