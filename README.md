# HakuBot

基于 Go / ZeroBot 的自用 OneBot V11 Bot。当前提供启动装配、应用核心和公共工具，已接入 AI 助手、状态监控和 B 站链接解析插件。Python 版结构见 [ARCHITECTURE.md](ARCHITECTURE.md)，重写目标和进度见 [GOAL.md](GOAL.md)。

## 构建与配置

`config_example/` 集中存放配置示例，统一使用 4 空格缩进；`config/` 存放真实配置，整个目录由 Git 忽略。两个目录的相对路径保持一致。

使用 Go 1.25.1 或更新版本，在项目根目录执行：

```bash
go mod download
go build ./...
```

`config_example/bot.json` 是配置示例。实际配置保存为 `config/bot.json`，填写现有 OneBot 服务地址、Token 和超级用户；`reverse_websocket` 决定使用 ZeroBot 的正向客户端还是反向服务端。配置读取不会回写文件。

完成配置并准备接入时，启动命令为：

```bash
go run . -config config/bot.json
```

`root` 默认相对启动目录解析，可留空使用 `HAKUBOT_ROOT`。浏览器渲染的 `chromium` 可指定已有 Chromium 可执行文件，留空由 chromedp 查找系统浏览器。程序不会自动安装浏览器或复制 Python 项目的资源。

## 目录与调用方式

| 位置 | 内容 |
| --- | --- |
| `main.go` | 读取配置、装配公共资源、启动 ZeroBot、处理退出信号 |
| `core/config.go` | Bot 配置转换为 ZeroBot 配置，直接使用框架驱动 |
| `core/bootstrap.go` | 共享 HTTP 会话、浏览器、缓存、临时文件与生命周期的装配 |
| `core/access.go` | 按群的开关、子功能、超管豁免、CD、健康状态与水印；状态读写加锁 |
| `core/registry.go` | 沿用 Python 版的可管理功能 ID，不表示这些插件已实现 |
| `core/settings.go` | 插件配置读取、保留未知字段与数字精度的局部更新 |
| `core/lifecycle.go` | 带 context 的后台任务、周期任务、逆序资源关闭 |
| `utils/paths.go` / `json.go` / `files.go` | 路径、原子写入、临时文件、并发限制和文本截断 |
| `utils/logging/` | 统一业务与 ZeroBot 日志的格式、级别及模块/事件上下文 |
| `utils/network/` | HTTP 会话、代理、限量下载、原子文件下载、图片和头像获取 |
| `utils/llm/` | OpenAI 兼容聊天、生图、图片编辑、用量、可选 JSON 修复 |
| `utils/images/` | 图片解码与编码、裁切缩放、拼接、颜色处理、GIF 拆帧/编码、APNG 编码 |
| `utils/onebot/` | 消息提取、精确命令规则、合并转发降级、帮助发送、可选共享媒体文件 |
| `utils/rendering/` | Chromium 截图、Go HTML 模板、Markdown/高亮/公式、帮助图分页与缓存、字体 |
| `utils/rendering/draw/` | 直接使用 gg 的画布和绘图 API，补充中文折行、图文拼接和排行榜 |

插件通过普通注册函数使用 `zero.New()` 创建自己的 Engine，必要时挂载 `app.Access.Rule(id)`。调用 `SetCD` 设置冷却，`CheckCD` 查询剩余秒数，业务完成后调用 `UpdateCD`。检查与更新刻意分开，是否只允许同群单次执行由具体游戏或业务决定。

后台工作通过 `app.Runtime.Go` / `Every` 注册，必须响应传入 context；资源关闭函数通过 `AddCloser` 登记。`Close` 取消任务、等待其返回，然后逆序关闭资源。ZeroBot 的连接与重连完全由自带驱动负责，未增加自建连接层。

HTTP 客户端的空代理继承环境代理，`direct` 明确直连，也可传指定代理 URL。LLM 配置可从 `llm.DefaultConfig()` 开始读取，实例在所属插件关闭时释放；不跨插件借用密钥或模型。

图片、音频、回复、@ 和普通发送直接使用 ZeroBot 的 `message` / `Ctx`。已有字节优先使用 `ImageBytes`；确需共享文件时使用 `app.Media`，路径由 `NAPCAT_SHARED_HOST_ROOT` / `NAPCAT_SHARED_CONTAINER_ROOT` 控制。合并转发结果未知时不补发，只有明确拒绝并允许降级时才逐条发送。

## 与 Python 版的对应边界

- 保留现有功能 ID、状态 JSON 的群/用户/功能键、秒级时间戳及 `waifu`、`alive_stats`、`sekai_cache`、`identify` 的路径例外。已原样复制 AI 助手和状态监控的真实配置并由 Git 忽略，尚未迁移业务数据。
- NoneBot 事件模型、消息构造和驱动由 ZeroBot 替代；日志通过 `utils/logging` 统一使用 Logrus，Python 线程池由 goroutine 和并发限额替代。
- 公共帮助模板已改写为 Go 模板，保留日夜主题和分页脚本。业务插件中的 Jinja 模板要在相应插件迁移时转换，不能直接交给 Go 模板引擎。
- `painter/plot` 的 Python 类链式 API 不逐类复制，画布、图形、渐变直接用 gg，布局使用已有绘图工具。字体、emoji、Markdown 扩展和排行榜像素效果仍需在具体插件中对照调整，不承诺与 Pillow/Jinja 输出逐像素一致。
- GIF 支持透明阈值和 disposal 合成；APNG 支持完整 RGBA 帧。Go 标准库及 x/image 负责静态格式解码，动画 WebP 不在本次实现范围。
- JSON 修复必须显式开启；Go 模型字段的业务校验由对应插件完成，不引入一套 Pydantic 仿制框架。
- `core/index_cli.py` 实际调用 PJSK 业务索引服务，留在 PJSK 插件迁移阶段实现；没有生成空的 CLI 占位入口。
- 定时器当前提供固定间隔任务。原 APScheduler 的 cron 时刻、上海时区日切和各插件调度逻辑在迁移对应业务时接入。

当前验证为 `gofmt` 和 `go build ./...`。未启动 Bot，未调用外部 LLM 或 OneBot，未运行截图或发送业务消息；未新增或运行测试。

## 日志

业务与 ZeroBot 共用 Logrus 全局 logger，默认 Info 级别、标准错误输出、不带 ANSI 颜色。时间使用进程本地时区。启动参数 `-log-level debug` 开启调试日志，`-log-color` 显式开启控制台颜色；重定向与 journald 默认得到纯文本。输出目标也可由入口调用 `logging.Init` 指定，不内置文件轮转或异步队列。

```go
log := logging.Module("ai_assistant")
log.WithField("group_id", groupID).Info("开始生成回复")
log.WithError(err).Error("生成回复失败")

// 在事件处理器内，按需附加 Bot、群、用户和消息 ID。
logging.Event("ai_assistant", ctx.Event).Info("开始处理消息")
```

`Module` 和 `Event` 返回原生 `*logrus.Entry`，继续使用 `WithField`、`WithFields`、`WithError` 即可。没有 module 字段的框架日志标为 `zerobot`，保留框架原有的 `[bot]`、`[ws]`、`[api]` 消息前缀和异常堆栈。字段按名称排序输出。

## AI 助手

`plugins/ai_assistant` 已接入启动入口。配置位于 `config/plugins/ai_assistant/config.json`，完整字段和默认值见 `config_example/plugins/ai_assistant/config.json`；未提供有效配置时只跳过 AI 命令并记录初始化错误。AI 助手的真实配置已从 Python 版原样复制，仅保留在本地并由 Git 忽略。

| 命令（前缀使用 Bot 配置） | 功能 |
| --- | --- |
| `chat` | 单次多模态对话，按配置自动联网 |
| `chat联网` / `chat_web` / `chatweb` / `chat搜索` | 强制 Tavily 搜索后回答 |
| `生图` | 文生图；带当前或回复图片时调用图片编辑 |
| `生图联网` / `生图web` / `生图搜索` | 搜索、视觉设定提炼、生图或编辑 |
| `切换模型` / `更改模型` / `change_model` | 超管确认新聊天模型可用后保存配置 |

保留查询规则提取和 LLM 重写、全部搜索参数、模块级聊天连接覆盖、参考图压缩、合并转发展开限制、Markdown 背景和水印、群聊子功能开关与 CD。视觉提炼使用搜索文本和图片描述，搜索图片不会自动作为编辑参考图上传。没有会话历史或数据库。

自动搜索失败时会提示模型无法确认最新信息，强制联网在全部查询失败时直接报告错误；搜索成功但无结果与网络失败分别处理。`query_rewrite=false` 现在直接使用用户搜索文本，不再暗中做规则改写。CD 在最终聊天或生图调用成功后更新；查询重写和视觉提炼的额外 Token 不计入“本次回答 Token”。

实现按注册与管理、配置、消息输入、对话、生图、搜索拆为六个文件，共享 HTTP 和 LLM 客户端。AI 页面使用 `plugins/ai_assistant/templates/reply.html`，沿用原 Markdown 模板的 article 结构、45px 内边距及小屏 15px 内边距；背景和水印由该模板根据配置填充。原 `github-markdown-light.css`、`pygments-default.css` 保留在公共模板资源目录，高亮输出 CSS 类名以使用原样式。通用渲染层提供 Markdown 转换和模板执行，不再在 Go 字符串里设计页面布局，也不再写入 `data/ai_assistant/custom_markdown.css`。文字降级保留 Markdown 原文，避免破坏代码和公式。图片格式能力补充了 EXIF 方向处理和 WebP 编码；WebP 编码依赖 CGO，构建环境需具备 C 编译器。

本次仅通过构建和源码核对，尚未接入实际 QQ、Tavily、LLM 或 Chromium 做端到端运行。

真实的 `config/bot.json` 已根据 Python `.env.prod` 和现有 OneBotFilter 路由生成，沿用昵称、超管和反向 WS 设置，Chromium 指向已有浏览器可执行文件。`command_prefixes` 可配置多个命令前缀（例如 `["", "."]`），省略时使用 `command_prefix`；AI 命令支持列表中的所有前缀。当前未启动 Go Bot，也未修改现有服务路由。

## 状态监控

`alive_stat` 已接入启动入口，支持 `alive`、`alive day`、`alive night` 以及配置的所有命令前缀。群聊遵循插件开关和超管豁免，不增加 CD。收到命令时并行采集 CPU（0.5 秒采样）、内存、Swap、根分区磁盘、进程、Docker 容器和 ping 结果，不持续轮询或主动告警。

真实配置已原样复制到 `config/plugins/alive_stat/config.json`，示例在 `config_example/plugins/alive_stat/config.json`，字段保持 `autochat_data_file`、`monitored_processes`、`docker_processes`、`ping_hosts`。真实配置由 Git 忽略，进程关键词没有自动改写。Docker 查询需要当前用户可执行 Docker CLI，网络检测使用系统 ping；无法采集的项显示 Unknown/N/A 并记录日志。进程内存是包含子进程、按 PID 去重后的 RSS 总和，不代表独占物理内存。

状态卡沿用 900px 宽度、28px 外边距、32px 内边距、26px 区块间距、日夜配色、背景渐变、双列运行时间、进度条、装饰和水印。原字体已复制。`render.go` 组织布局与样式，所有具体绘制由公共 `utils/rendering/draw` 的 Scene、文字测量、圆角框和进度条完成，不使用浏览器或插件私有绘图引擎。

HakuBot 统计继续使用 `data/alive_stats/stats.json` 和原有四个字段，每五分钟及退出时原子保存。累计值为启动时读取的总时长加本次运行时长，缺少文件时从零开始。Autochat 文件只读，时长计算及缺失值行为与 Python 版一致，不增加在线判断。

本次未移动、复制或修改统计文件，也没有添加迁移工具、兼容层或双读逻辑。正式切换时停止 Python 版，将最新 `stats.json` 直接移到 Go 工作区对应目录，再启动 Go 版；届时根据最终启动命令调整监控关键词。

已完成源码核对、格式化和 `go build ./...`。未启动 Bot、执行实际监控查询或发送图片，状态卡视觉效果尚未实际联调；未新增或运行测试。

## B 站链接解析

`plugins/analysis_bilibili` 已接入启动入口，自动识别并解析消息与分享卡片中的 B 站内容，支持视频、番剧、直播间、专栏和动态五类内容，并在开启时支持 `搜视频` 命令。配置位于 `config/plugins/analysis_bilibili/config.json`，完整选项与默认值见 `config_example/plugins/analysis_bilibili/config.json`。真实配置由 Git 忽略；默认配置为只发送文字、关闭重复限制、关闭显式搜视频命令、网络直连。

- **输入识别与规范化**：优先从原始 JSON 消息段中解析小程序/卡片跳转链接；支持多段 JSON 遍历、短链展开（最多 5 次且严格校验合法 B 站域名）及开头的裸 BV/av/cv 号。卡片无链接但包含标题时保留搜索解析能力。
- **输出合同与展示**：按原版字段与顺序组织展示，严格保留数字格式化（>10000 转换为“万”保留两位小数）、本地时区时间转换与分 P / 时间定位拼接。简介黑名单群仅隐藏视频和番剧的简介，不影响其他正文或动态。
- **图片与降级**：按类型开关控制是否展示图片；图片尺寸追加 CDN 后缀并保留查询参数；使用公共限量下载与内容校验；被 OneBot 明确拒绝时自动尝试一次纯文字降级。
- **去重与凭据缓存**：去重以 `(Bot ID, 会话类型, 会话 ID, 标准 URL)` 为键，仅在内存中维护，支持 TTL 过期清理；WBI 签名密钥缓存 1 小时，ticket 凭据缓存 2 小时并严格限制作用域为 `.bilibili.com`，网络请求期间不持有去重锁。

代码迁移已收尾，审核反馈已修复，最终复审未发现新的需修复问题；构建、格式检查和 `git diff --check` 通过。真实配置按 Python 原版默认值新建，无持久化数据需要迁移。未启动 Bot、未调用外部 B 站接口进行端到端联调；未新增或运行测试。

搜索与自动解析共用原始文本命令判断；业务错误提示保留，底层网络错误转为简洁中文。结果与提示发送均使用继承应用取消的 30 秒 context；异步接受或结果未知时不重复补发。当前 ZeroBot 的 socket 写入阶段没有 deadline，30 秒并非整个发送调用的严格时间上限，后续跟进见 `GOAL.md`。
