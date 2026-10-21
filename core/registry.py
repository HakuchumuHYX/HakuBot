from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureSpec:
    id: str
    name: str
    # 定时推送类默认关：不请自来的推送只发给显式开启的群
    default: bool = True
    # 只有代码里检查了 CD 的功能才为 True，CD 命令只接受这些
    cooldown: bool = False


FEATURES = (
    FeatureSpec("draw_lots", "赛博浅草寺（抽签）"),
    FeatureSpec("deer_pipe", "鹿签到", cooldown=True),
    FeatureSpec("analysis_bilibili", "B 站链接解析"),
    FeatureSpec("help_plugin", "帮助文档"),
    FeatureSpec("two_choices", "二择"),
    FeatureSpec("welcome", "欢迎新群员"),
    FeatureSpec("jrrp", "今日人品"),
    FeatureSpec("plus_one", "复读姬"),
    FeatureSpec("sticker_saver", "动画表情保存"),
    FeatureSpec("atri_reply", "呼叫 ATRI"),
    FeatureSpec("daily_message", "定时消息", default=False),
    FeatureSpec("identify", "鉴定"),
    FeatureSpec("group_statistics", "群聊消息统计"),
    FeatureSpec("groupmate_waifu", "娶群友"),
    FeatureSpec("groupmate_waifu:yinpa", "涩群友"),
    FeatureSpec("groupmate_waifu:bye", "分手", cooldown=True),
    FeatureSpec("poke_reply", "戳一戳"),
    FeatureSpec("poke_reply:poke", "戳一戳回复", cooldown=True),
    FeatureSpec("poke_reply:contribute", "戳一戳投稿", cooldown=True),
    FeatureSpec("pjsk_guess_song", "PJSK 猜歌"),
    FeatureSpec("pjsk_guess_song:listen", "PJSK 听歌"),
    FeatureSpec("send_and_reply", "私戳 bot 主"),
    FeatureSpec("stickers", "随机表情包", cooldown=True),
    FeatureSpec("image_processor", "图片处理"),
    FeatureSpec("image_processor:reverse", "gif 倒放", cooldown=True),
    FeatureSpec("image_processor:speed", "gif 加速", cooldown=True),
    FeatureSpec("image_processor:symmetry", "图片对称", cooldown=True),
    FeatureSpec("image_processor:cutout", "图片抠图", cooldown=True),
    FeatureSpec("image_processor:video_to_gif", "视频转 GIF", cooldown=True),
    FeatureSpec("image_processor:mirror", "图片镜像", cooldown=True),
    FeatureSpec("image_processor:rotate", "图片旋转", cooldown=True),
    FeatureSpec("ai_assistant", "AI 助手"),
    FeatureSpec("ai_assistant:chat", "AI 对话", cooldown=True),
    FeatureSpec("ai_assistant:imagen", "AI 生图", cooldown=True),
    FeatureSpec("recall", "撤回插件"),
    FeatureSpec("recall:self_recall", "自我撤回"),
    FeatureSpec("lunabot_imgexp", "搜图插件"),
    FeatureSpec("lunabot_imgexp:search", "搜图功能", cooldown=True),
    FeatureSpec("lunabot_imgexp:ximg", "推特图片", cooldown=True),
    FeatureSpec("group_daily_analysis", "每日群总结", default=False),
    FeatureSpec("group_daily_analysis:topics", "每日事件总结"),
    FeatureSpec("group_daily_analysis:user_titles", "群友画像总结"),
    FeatureSpec("group_daily_analysis:golden_quotes", "金句锐评"),
    FeatureSpec("setu_plugin", "来张涩图", cooldown=True),
    FeatureSpec("alive_stat", "存活统计"),
    FeatureSpec("sk_predict", "PJSK活动预测"),
    FeatureSpec("pjsk_event_summary", "PJSK剧情"),
    FeatureSpec("pjsk_guess_card", "PJSK猜卡面"),
    FeatureSpec("pixiv_id_fetcher", "Pixiv ID取图", cooldown=True),
    FeatureSpec("juya_daily_fetcher", "橘鸦 AI 早报", default=False),
)

FEATURES_BY_ID = {item.id: item for item in FEATURES}
