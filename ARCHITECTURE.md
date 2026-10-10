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

帮助图也由各插件自己的模板渲染；全局菜单继续手写，渲染失败时发送同源文本。`utils/rendering/templates/` 只保留通用的 Markdown / 纯文本转图模板。

群日报和早报各用自己的 LLM 配置，不读取其他插件的配置作为回退。正常运行使用 `uv run python bot.py`。
