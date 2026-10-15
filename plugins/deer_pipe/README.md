# 🦌管签到插件 (deer_pipe)

一个基于 NoneBot2 的趣味签到插件，支持每日签到、补签和日历查看功能。

## 功能特性

- 🦌 每日签到
- 🦌 帮他人签到（可配置）
- 🦌 补签过去日期（可配置）
- 🦌 查看签到日历
- 📊 自动清理过期数据

## 命令列表

| 命令                       | 说明               |
| -------------------------- | ------------------ |
| `🦌` 或 `鹿`               | 签到一次           |
| `🦌 @xxx` 或 `鹿 @xxx`     | 帮他人签到         |
| `补🦌 x` 或 `补鹿 x`       | 补签本月第 x 日    |
| `🦌历` 或 `鹿历`           | 查看自己的签到日历 |
| `🦌历 @xxx` 或 `鹿历 @xxx` | 查看他人的签到日历 |

## 配置项

在 `config.py` 中可以修改以下配置：

```python
class DeerPipeConfig(BaseModel):
    # CD 时间（秒），0 表示无冷却
    cd_time: int = 0

    # 是否启用补签功能
    enable_past_deer: bool = True

    # 是否启用帮他人签到功能
    enable_help_deer: bool = True

    # 日历图片质量 (1-100)
    image_quality: int = 95

    # 签到成功时的提示语
    success_message: str = "成功🦌了"
    help_success_message: str = "成功帮{target}🦌了"
    past_success_message: str = "成功补🦌"

    # 错误提示语
    disabled_message: str = "🦌签到功能当前已被禁用"
    cd_message: str = "🦌功能还在冷却中，请等待 {remaining} 秒"
    invalid_date_message: str = "不是合法的补🦌日期捏"
    already_signed_message: str = "不能补🦌已经🦌过的日子捏"
```

## 依赖插件

- `nonebot_plugin_alconna` - 命令解析
- `nonebot_plugin_apscheduler` - 定时任务
- `nonebot_plugin_localstore` - 本地存储
- `nonebot_plugin_userinfo` - 用户信息获取

## 文件结构

```
deer_pipe/
├── __init__.py      # 插件入口
├── config.py        # 配置定义
├── constants.py     # 常量定义
├── database.py      # 数据库操作
├── image.py         # 图片生成
├── matchers.py      # 命令处理器
├── requirements.py  # 依赖加载
├── py.typed         # 类型提示标记
├── README.md        # 说明文档
└── assets/          # 静态资源
    ├── check@96x100.png
    ├── deerpipe@100x82.png
    └── MiSans-Regular.ttf
```

## 更新日志

### v1.0.1 (重构版本)

- ✨ 新增配置文件，支持自定义提示语和功能开关
- ✨ 新增年份字段，修复跨年数据问题
- 🔧 修复数据库初始化逻辑
- 🔧 优化图片生成，使用内存缓冲区替代磁盘 I/O
- 🔧 优化资源加载，改为延迟加载模式
- 🔧 优化日志输出，使用 NoneBot 内置日志系统
- 🔧 优化异常处理，正确处理 FinishedException
- 🔧 优化代码结构，移除冗余注释
- 📝 添加完整的代码文档和类型注解

## 原作者

- [SamuNatsu](https://github.com/SamuNatsu)
- 原仓库: https://github.com/SamuNatsu/nonebot-plugin-deer-pipe

## 许可

本插件衍生自原仓库，按原项目的许可证 [GNU AGPL-3.0](LICENSE) 发布，不适用 HakuBot 根目录的 MIT 许可证。
