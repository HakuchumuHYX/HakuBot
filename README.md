# HakuBot

基于 Go / ZeroBot 的自用 OneBot V11 Bot。当前提供启动装配、应用核心和公共工具，尚未注册业务插件。Python 版结构见 [ARCHITECTURE.md](ARCHITECTURE.md)，重写目标和进度见 [GOAL.md](GOAL.md)。

## 构建与配置

使用 Go 1.25.1 或更新版本，在项目根目录执行：

```bash
go mod download
go build ./...
```

`config/bot.example.json` 是配置示例。实际配置保存为 `config/bot.json`，填写现有 OneBot 服务地址、Token 和超级用户；`reverse_websocket` 决定使用 ZeroBot 的正向客户端还是反向服务端。配置读取不会回写文件。

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

- 保留现有功能 ID、状态 JSON 的群/用户/功能键、秒级时间戳及 `waifu`、`alive_stats`、`sekai_cache`、`identify` 的路径例外。没有复制任何私密配置或业务数据。
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
