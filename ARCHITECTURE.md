# HakuBot 结构说明

- `plugins/`：插件入口、命令处理及各自的业务逻辑。
- `core/`：应用装配、插件开关、冷却、配置访问和资源生命周期。
- `utils/`：公共网络、文件、图片、OneBot 消息及渲染能力。
- `config/plugins/<插件名>/`：插件配置，文件内容保持原样。
- `data/shared/fonts/`：公共字体。
- `data/shared/playwright/`：浏览器资源。
- `data/shared/rembg_models/`：抠图模型。
- `cache/shared/rendering/`：公共渲染缓存。

公共工具不依赖具体插件。业务数据通过 `PluginPaths` 访问，现有记录和外部路径保持有效；不需要额外的迁移或验收流程。

帮助图片由同一份内容生成浅蓝或夜间卡片，按上海时间自动切换：06:00–18:00 使用日间主题，其余使用夜间主题。全局菜单继续手写，渲染失败时发送同源文本。

帮助模板集中在 `utils/rendering/templates/help.html`。赛事、赛果、统计和日报主题等业务模板保留在所属插件，由 `utils/rendering/engine.py` 统一渲染。

群日报和早报各用自己的 LLM 配置，不读取其他插件的配置作为回退。正常运行使用 `uv run python bot.py`。
