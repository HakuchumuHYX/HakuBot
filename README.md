# HakuBot

<div align="center">

![Python](https://img.shields.io/badge/Python-3.9+-blue?style=flat-square)
![NoneBot](https://img.shields.io/badge/NoneBot-2.0+-green?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

</div>

> [!WARNING]
> **这是一个基于 [NoneBot](https://nonebot.dev) 的自用 Bot。**  
> 如果直接下载源码进行部署，可以从uv.lock中获取所需依赖作为参考。
>
> Plugin 列表中所使用的部分插件来自于 [NoneBot](https://nonebot.dev) 插件商店以及 [AstrBot](https://docs.astrbot.app/) 插件商店，并由我进行了一些魔改。部分插件中集成了管理插件（plugin_manager），如果想要将插件独立下载使用请注意。

## 🧩 插件列表

### 🎸 PJSK 相关

- **[sk-predict](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/cnsk_predict)**: PJSK 预测线。本质上是访问榜线预测api获得结果并进行手动渲染。可访问[榜线预测站](https://pjsk.moe/prediction/)。
- **[pjsk_event_summary](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/pjsk_event_summary)**: PJSK 剧情总结，数据来源于 [PJSK 剧情站](https://sekaistory.exmeaning.com/)。
- **[pjsk_guess_song](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/pjsk_guess_song)**: PJSK 猜歌以及听歌插件。主要逻辑来自于 [astrbot-plugin-pjsk-guess-song](https://github.com/nichinichisou0609/astrbot_plugin_pjsk_guess_song) 并由我进行了一些修改。
- **[pjskprofile_snowybot](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/pjskprofile_snowybot)**: 获取SnowyBot版的PJSK个人信息（已废弃）。

### 🛠️ 实用工具 & 管理

- **[plugin_manager](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/plugin_manager)**: 插件管理核心，基本上把所有的插件以及子功能全部接入了管理。
- **[ai_assistant](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/ai_assistant)**: 群 AI 助手。目前支持 chat、生图以及点歌服务。
  - Chat 和生图功能可以通过加 web 后缀来进行联网搜索信息。
  - 点歌会调用 LLM 自行决定是否联网搜索信息（由 [astrbot_plugin_music](https://github.com/Zhalslar/astrbot_plugin_music) 重构而来）。
- **[analysis_bilibili](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/analysis_bilibili)**: B站链接解析。移植自 [nonebot-plugin-analysis-bilibili](https://github.com/mengshouer/nonebot_plugin_analysis_bilibili)。
- **[daily_message](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/daily_message)**: 群聊定时消息推送。
- **[help_plugin](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/help_plugin)**: 返回图片形式的帮助文档。
- **[image_processor](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/image_processor)**: 图像处理工具箱（GIF 倒放/倍速/视频转 GIF、抠图、镜像、旋转、对称）。
- **[lunabot_imgexp](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/lunabot_imgexp)**: 多来源搜图以及 [X](https://x.com/) 图片获取。从 [lunabot](https://github.com/NeuraXmy/lunabot) 中解耦修改而来。
- **[recall](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/recall)**: 撤回相关功能。支持 Bot 主动撤回消息，或监听群聊撤回消息。
- **[send_and_reply](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/send_and_reply)**: 让群友快速私戳 Bot 主的功能。
- **[sticker_saver](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/sticker_saver)**: 表情包保存。移植自 [nonebot-plugin-sticker-saver](https://github.com/colasama/nonebot-plugin-sticker-saver)。
- **[stickers](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/stickers)**: 随机表情包获取，支持定向查看、群友投稿、SU 管理等。
- **[alive-stat](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/alive_stat)**: 查看 Bot 当前运行时间以及服务器状态。
- **[welcome](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/welcome)**: 简单的入群欢迎插件。

### 🎮 娱乐 & 趣味

- **[deer_pipe](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/deer_pipe)**: 鹿签到插件。修改自 [nonebot-plugin-deer-pipe](https://github.com/SamuNatsu/nonebot-plugin-deer-pipe)。
- **[draw_lots](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/draw_lots)**: 抽签插件。脱胎于 [nonebot-plugin-CyberSensoji](https://github.com/Raidenneox/nonebot_plugin_CyberSensoji)。
- **[groupmate_waifu](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/groupmate_waifu)**: 娶群友插件。脱胎于 [nonebot-plugin-groupmate-waifu](https://github.com/KarisAya/nonebot_plugin_groupmate_waifu)，经过大幅度重构。
- **[identify](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/identify)**: 成分鉴定（鉴定 0/1），支持鉴定自己或群友，每日刷新。
- **[jrrp](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/jrrp)**: 今日人品值。移植自 [nonebot-plugin-jrrp](https://github.com/SkyDynamic/nonebot_plugin_jrrp)。
- **[poke_reply](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/poke_reply)**: 戳一戳回复，支持分群管理、内容投稿以及 SU 管理。
- **[two_choices](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/two_choices)**: 简单的二择插件。
- **[atri_reply](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/atri_reply)**: 关键词检测回复（Atri 风格）。
- **[plus_one](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/plus_one)**: 复读姬。移植修改自 [nonebot-plugin-plus-one](https://github.com/yejue/nonebot-plugin-plus-one)。
- **[hyw](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/hyw)**: 非常意义不明的插件。
- **[setu_plugin](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/setu_plugin)**: 简单的涩图获取插件。解耦自 [ATRI](https://github.com/Kyomotoi/ATRI)。

### 📊 统计 & 订阅

- **[group_daily_analysis](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/group_daily_analysis)**: 基于 LLM 的群聊分析插件。从 [astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis) 重构且微作修改后得来，引入json-repair库，增强解析json能力。
- **[group_statistics](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/group_statistics)**: 简单的本日群聊消息统计插件。
- **[hltv_sub](https://github.com/HakuchumuHYX/HakuBot/tree/main/plugins/hltv_sub)**: [HLTV](https://www.hltv.org/) 比赛信息推送，支持手动及自动推送。

## 🙏 特别感谢

> 排名不分先后

- [NoneBot](https://github.com/nonebot/nonebot2)
- 代码指导以及api来源: [DeepSeek](https://chat.deepseek.com/) | [ChatGPT](https://chatgpt.com/) | [Gemini](https://gemini.google.com/)
- 灵感与代码来源:
  - [ATRI](https://github.com/Kyomotoi/ATRI)
  - [astrbot_plugin_music](https://github.com/Zhalslar/astrbot_plugin_music)
  - [astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis)
  - [nonebot-plugin-analysis-bilibili](https://github.com/mengshouer/nonebot_plugin_analysis_bilibili)
  - [nonebot-plugin-deer-pipe](https://github.com/SamuNatsu/nonebot-plugin-deer-pipe)
  - [nonebot-plugin-CyberSensoji](https://github.com/Raidenneox/nonebot_plugin_CyberSensoji)
  - [nonebot-plugin-groupmate-waifu](https://github.com/KarisAya/nonebot_plugin_groupmate_waifu)
  - [nonebot-plugin-jrrp](https://github.com/SkyDynamic/nonebot_plugin_jrrp)
  - [astrbot-plugin-pjsk-guess-song](https://github.com/nichinichisou0609/astrbot_plugin_pjsk_guess_song)
  - [nonebot-plugin-plus-one](https://github.com/yejue/nonebot-plugin-plus-one)
  - [nonebot-plugin-sticker-saver](https://github.com/colasama/nonebot-plugin-sticker-saver)
  - [astrbot-plugin-stickers](https://github.com/shiywhh/astrbot_plugin_sticker)
