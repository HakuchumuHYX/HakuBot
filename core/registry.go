package core

type FeatureSpec struct{ ID, Name string }

// Features describes manageable functions, not the set of loaded plugins.
var Features = []FeatureSpec{
	{"draw_lots", "赛博浅草寺（抽签）"},
	{"deer_pipe", "鹿签到"},
	{"analysis_bilibili", "B 站链接解析"},
	{"help_plugin", "帮助文档"},
	{"two_choices", "二择"},
	{"welcome", "欢迎新群员"},
	{"jrrp", "今日人品"},
	{"plus_one", "复读姬"},
	{"sticker_saver", "动画表情保存"},
	{"atri_reply", "呼叫 ATRI"},
	{"daily_message", "定时消息"},
	{"identify", "鉴定"},
	{"group_statistics", "群聊消息统计"},
	{"groupmate_waifu", "娶群友"},
	{"groupmate_waifu:yinpa", "涩群友"},
	{"groupmate_waifu:bye", "分手"},
	{"poke_reply", "戳一戳"},
	{"poke_reply:poke", "戳一戳回复"},
	{"poke_reply:contribute", "戳一戳投稿"},
	{"pjsk_guess_song", "PJSK 猜歌"},
	{"pjsk_guess_song:listen", "PJSK 听歌"},
	{"send_and_reply", "私戳 bot 主"},
	{"stickers", "随机表情包"},
	{"image_processor", "图片处理"},
	{"image_processor:reverse", "gif 倒放"},
	{"image_processor:speed", "gif 加速"},
	{"image_processor:symmetry", "图片对称"},
	{"image_processor:cutout", "图片抠图"},
	{"image_processor:video_to_gif", "视频转 GIF"},
	{"image_processor:mirror", "图片镜像"},
	{"image_processor:rotate", "图片旋转"},
	{"ai_assistant", "AI 助手"},
	{"ai_assistant:chat", "AI 对话"},
	{"ai_assistant:imagen", "AI 生图"},
	{"recall", "撤回插件"},
	{"recall:self_recall", "自我撤回"},
	{"lunabot_imgexp", "搜图插件"},
	{"lunabot_imgexp:search", "搜图功能"},
	{"lunabot_imgexp:ximg", "推特图片"},
	{"group_daily_analysis", "每日群总结"},
	{"group_daily_analysis:topics", "每日事件总结"},
	{"group_daily_analysis:user_titles", "群友画像总结"},
	{"group_daily_analysis:golden_quotes", "金句锐评"},
	{"setu_plugin", "来张涩图"},
	{"alive_stat", "存活统计"},
	{"sk_predict", "PJSK活动预测"},
	{"pjsk_event_summary", "PJSK剧情"},
	{"pjsk_guess_card", "PJSK猜卡面"},
	{"pixiv_id_fetcher", "Pixiv ID取图"},
}

func Catalog() map[string]string {
	result := make(map[string]string, len(Features))
	for _, f := range Features {
		result[f.ID] = f.Name
	}
	return result
}
