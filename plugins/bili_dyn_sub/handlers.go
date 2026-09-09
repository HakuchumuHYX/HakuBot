package bili_dyn_sub

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/onebot"
	zero "github.com/wdvxdr1123/ZeroBot"
)

var (
	uidURLRegex    = regexp.MustCompile(`space\.bilibili\.com/(\d{1,20})`)
	uidPrefixRegex = regexp.MustCompile(`(?i)^uid\s*[:：]\s*(\d{1,20})$`)
	uidPlainRegex  = regexp.MustCompile(`^(\d{1,20})$`)
	groupIDRegex   = regexp.MustCompile(`^\d{1,15}$`)
)

var textModeKeywords = map[string]bool{
	"text": true,
	"txt":  true,
	"文本":   true,
	"纯文本":  true,
}

func parseUID(raw string) string {
	text := strings.TrimSpace(raw)
	if text == "" {
		return ""
	}
	for _, re := range []*regexp.Regexp{uidURLRegex, uidPrefixRegex, uidPlainRegex} {
		m := re.FindStringSubmatch(text)
		if len(m) > 1 {
			trimmed := strings.TrimLeft(m[1], "0")
			return trimmed
		}
	}
	return ""
}

func parseGroupID(raw string) (int64, bool) {
	text := strings.TrimSpace(raw)
	if !groupIDRegex.MatchString(text) {
		return 0, false
	}
	val, err := strconv.ParseInt(text, 10, 64)
	if err != nil || val <= 0 {
		return 0, false
	}
	return val, true
}

func splitCommandArgs(raw string) (string, string) {
	parts := strings.Fields(strings.TrimSpace(raw))
	if len(parts) == 0 {
		return "", ""
	}
	if len(parts) == 1 {
		return parts[0], ""
	}
	return parts[0], parts[1]
}

func uidUsage(command string, private, urlForm bool) string {
	if private {
		lines := []string{
			"请提供 UP 主 UID 与目标群号，例如：",
			fmt.Sprintf("%s 13148307 819157441", command),
		}
		if urlForm {
			lines = append(lines, fmt.Sprintf("%s https://space.bilibili.com/13148307 819157441", command))
		}
		return strings.Join(lines, "\n")
	}
	lines := []string{
		"请提供 UP 主 UID，例如：",
		fmt.Sprintf("%s 13148307", command),
	}
	if urlForm {
		lines = append(lines, fmt.Sprintf("%s https://space.bilibili.com/13148307", command))
	}
	lines = append(lines, fmt.Sprintf("（如需作用于其他群，在末尾追加群号：%s 13148307 819157441）", command))
	return strings.Join(lines, "\n")
}

func groupIDUsage(command, groupRaw string, private bool) string {
	var head string
	if groupRaw != "" {
		head = fmt.Sprintf("群号「%s」不是有效的 QQ 群号（应为 1~15 位数字且大于 0），请检查后重试", groupRaw)
	} else {
		head = fmt.Sprintf("私聊使用「%s」必须显式指定目标群号（私聊没有当前群可以回落）", command)
	}
	tail := fmt.Sprintf("用法：%s <UID> <群号>\n例如：%s 13148307 819157441", command, command)
	if !private {
		tail += fmt.Sprintf("\n省略群号则作用于当前群：%s 13148307", command)
	}
	return head + "\n" + tail
}

func checkMembership(ctx context.Context, bot *zero.Ctx, groupID int64) string {
	type groupItem struct {
		GroupID int64 `json:"group_id"`
	}
	callCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()

	rsp := bot.CallActionWithContext(callCtx, "get_group_list", zero.Params{})
	if rsp.Status != "ok" || rsp.RetCode != 0 {
		return ""
	}

	groups := rsp.Data.Array()
	if len(groups) == 0 {
		return ""
	}

	found := false
	for _, g := range groups {
		if g.Get("group_id").Int() == groupID {
			found = true
			break
		}
	}
	if !found {
		return fmt.Sprintf("\n⚠️ 机器人当前不在群 %d 中，订阅已记录但推送会失败，请确认群号是否输错", groupID)
	}
	return ""
}

func formatSubscription(item SubscriptionItem, store *Store, bm *BackoffManager, showGroups bool) string {
	uid := item.UID
	name := item.Name
	if name == "" {
		name = "（未知昵称）"
	}
	lastSuccess := store.GetLastSuccess(uid)
	if lastSuccess == "" {
		lastSuccess = "从未成功"
	}
	line := fmt.Sprintf("• %s\n  UID %s | 上次成功 %s", name, uid, lastSuccess)
	if showGroups {
		var gStrs []string
		for _, g := range item.Groups {
			gStrs = append(gStrs, strconv.FormatInt(g, 10))
		}
		groupsText := "（无）"
		if len(gStrs) > 0 {
			groupsText = strings.Join(gStrs, "、")
		}
		line += "\n  推送群：" + groupsText
	}
	rem := bm.RemainingSeconds(uid, time.Now())
	if rem > 0 {
		line += fmt.Sprintf("\n  ⚠️ 退避中，剩余 %ds", rem)
	}
	if len(item.Categories) > 0 && len(item.Categories) < len(CategoryNames) {
		var catStrs []string
		for _, c := range item.Categories {
			if n, ok := CategoryNames[c]; ok {
				catStrs = append(catStrs, n)
			} else {
				catStrs = append(catStrs, strconv.Itoa(c))
			}
		}
		line += "\n  分类：" + strings.Join(catStrs, "/")
	}
	return line
}

func formatGlobalStatus(credMgr *CredentialManager) string {
	loginDesc := "未配置 sessdata（匿名取数）"
	if st := credMgr.GetLoginStatus(); st != nil {
		loginDesc = st.Summary()
	} else if credMgr.cfg.SessData != "" {
		loginDesc = "已配置 sessdata，尚未校验（启动后约 30s 完成首次校验）"
	}
	return fmt.Sprintf("🔑 %s\n   登录态：%s", credMgr.Describe(), loginDesc)
}

func sendReply(ctx context.Context, bot *zero.Ctx, msg any) {
	if ctx.Err() != nil {
		return
	}
	callCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()

	if bot.Event.GroupID > 0 {
		_ = bot.CallActionWithContext(callCtx, "send_group_msg", zero.Params{
			"group_id": bot.Event.GroupID,
			"message":  msg,
		})
	} else {
		_ = bot.CallActionWithContext(callCtx, "send_private_msg", zero.Params{
			"user_id": bot.Event.UserID,
			"message": msg,
		})
	}
}

func registerCommands(app *core.App, store *Store, credMgr *CredentialManager, api *APIClient, bm *BackoffManager, prefixes []string) {
	buildCommands := func(aliases []string) []string {
		var res []string
		for _, prefix := range prefixes {
			for _, alias := range aliases {
				res = append(res, prefix+alias)
			}
		}
		return res
	}

	// 1. b站订阅
	subCmds := buildCommands([]string{"b站订阅", "B站订阅", "b站动态订阅"})
	zero.New().OnMessage(onebot.ExactCommand(subCmds...), zero.SuperUserPermission).
		SetPriority(5).SetBlock(true).
		Handle(func(bot *zero.Ctx) {
			bot.NoTimeout()
			_ = app.Runtime.Do(func(ctx context.Context) error {
				args := bot.State["args"].(string)
				uidRaw, groupRaw := splitCommandArgs(args)
				isPrivate := bot.Event.GroupID == 0

				uid := parseUID(uidRaw)
				if uid == "" {
					sendReply(ctx, bot, uidUsage("b站订阅", isPrivate, true))
					return nil
				}

				targetGroup := bot.Event.GroupID
				note := ""
				if groupRaw != "" {
					g, ok := parseGroupID(groupRaw)
					if !ok {
						sendReply(ctx, bot, groupIDUsage("b站订阅", groupRaw, isPrivate))
						return nil
					}
					targetGroup = g
					if targetGroup != bot.Event.GroupID {
						note = checkMembership(ctx, bot, targetGroup)
					}
				} else if isPrivate {
					sendReply(ctx, bot, groupIDUsage("b站订阅", "", true))
					return nil
				}

				// Check already subscribed
				groups := store.GetGroups(uid)
				already := false
				for _, g := range groups {
					if g == targetGroup {
						already = true
						break
					}
				}
				if already {
					name := store.GetName(uid)
					if name == "" {
						name = "未知昵称"
					}
					sendReply(ctx, bot, fmt.Sprintf("群 %d 已订阅 UID %s（%s）%s", targetGroup, uid, name, note))
					return nil
				}

				// Fetch username
				uname, err := api.FetchUserName(ctx, uid)
				if err != nil || uname == "" {
					sendReply(ctx, bot, fmt.Sprintf("无法获取 UID %s 的昵称，可能是 UID 不存在或 B 站暂时不可访问，请确认后重试", uid))
					return nil
				}

				needBaseline := !store.IsBaselineInitialized(uid)
				store.AddSubscription(uid, uname, targetGroup, nil)

				tip := fmt.Sprintf("✅ 已为群 %d 订阅：%s\nUID %s\n轮询间隔约 %ds", targetGroup, uname, uid, credMgr.cfg.PollIntervalSeconds)
				if needBaseline {
					tip += "\n首轮只建立基线、不回推历史动态，之后的新动态才会推送"
				}
				sendReply(ctx, bot, tip+note)
				return nil
			})
		})

	// 2. b站退订
	unsubCmds := buildCommands([]string{"b站退订", "B站退订", "b站取消订阅"})
	zero.New().OnMessage(onebot.ExactCommand(unsubCmds...), zero.SuperUserPermission).
		SetPriority(5).SetBlock(true).
		Handle(func(bot *zero.Ctx) {
			bot.NoTimeout()
			_ = app.Runtime.Do(func(ctx context.Context) error {
				args := bot.State["args"].(string)
				uidRaw, groupRaw := splitCommandArgs(args)
				isPrivate := bot.Event.GroupID == 0

				uid := parseUID(uidRaw)
				if uid == "" {
					sendReply(ctx, bot, uidUsage("b站退订", isPrivate, false))
					return nil
				}

				targetGroup := bot.Event.GroupID
				if groupRaw != "" {
					g, ok := parseGroupID(groupRaw)
					if !ok {
						sendReply(ctx, bot, groupIDUsage("b站退订", groupRaw, isPrivate))
						return nil
					}
					targetGroup = g
				} else if isPrivate {
					sendReply(ctx, bot, groupIDUsage("b站退订", "", true))
					return nil
				}

				name := store.GetName(uid)
				if name == "" {
					name = "未知昵称"
				}

				if !store.RemoveSubscription(uid, targetGroup) {
					sendReply(ctx, bot, fmt.Sprintf("群 %d 未订阅 UID %s", targetGroup, uid))
					return nil
				}

				sendReply(ctx, bot, fmt.Sprintf("✅ 已为群 %d 退订：%s\nUID %s", targetGroup, name, uid))
				return nil
			})
		})

	// 3. b站订阅列表
	listCmds := buildCommands([]string{"b站订阅列表", "B站订阅列表"})
	zero.New().OnMessage(onebot.ExactCommand(listCmds...), zero.SuperUserPermission).
		SetPriority(5).SetBlock(true).
		Handle(func(bot *zero.Ctx) {
			bot.NoTimeout()
			_ = app.Runtime.Do(func(ctx context.Context) error {
				isPrivate := bot.Event.GroupID == 0
				globalStatus := formatGlobalStatus(credMgr)

				if !isPrivate {
					items := store.ListSubscriptions(bot.Event.GroupID)
					if len(items) == 0 {
						sendReply(ctx, bot, fmt.Sprintf("群 %d 暂无 B 站动态订阅\n使用「b站订阅 <UID>」添加\n%s", bot.Event.GroupID, globalStatus))
						return nil
					}
					var lines []string
					lines = append(lines, fmt.Sprintf("📋 群 %d 的 B 站动态订阅（%d 个）：", bot.Event.GroupID, len(items)), globalStatus)
					for _, it := range items {
						lines = append(lines, formatSubscription(it, store, bm, false))
					}
					sendReply(ctx, bot, strings.Join(lines, "\n"))
					return nil
				}

				// Private
				items := store.ListSubscriptions(0)
				if len(items) == 0 {
					sendReply(ctx, bot, fmt.Sprintf("当前没有任何 B 站动态订阅\n使用「b站订阅 <UID> <群号>」添加\n%s", globalStatus))
					return nil
				}
				groupSet := make(map[int64]bool)
				for _, it := range items {
					for _, g := range it.Groups {
						groupSet[g] = true
					}
				}
				var lines []string
				lines = append(lines, fmt.Sprintf("📋 全局 B 站动态订阅（%d 个 UP 主 / 覆盖 %d 个群）：", len(items), len(groupSet)), globalStatus)
				for _, it := range items {
					lines = append(lines, formatSubscription(it, store, bm, true))
				}
				sendReply(ctx, bot, strings.Join(lines, "\n"))
				return nil
			})
		})

	// 4. b站订阅测试
	testCmds := buildCommands([]string{"b站订阅测试", "B站订阅测试"})
	zero.New().OnMessage(onebot.ExactCommand(testCmds...), zero.SuperUserPermission).
		SetPriority(5).SetBlock(true).
		Handle(func(bot *zero.Ctx) {
			bot.NoTimeout()
			_ = app.Runtime.Do(func(ctx context.Context) error {
				args := bot.State["args"].(string)
				uidRaw, modeRaw := splitCommandArgs(args)

				uid := parseUID(uidRaw)
				if uid == "" {
					sendReply(ctx, bot, "请提供要测试的 UP 主 UID，例如：\nb站订阅测试 13148307        （完整渲染，与真实推送一致）\nb站订阅测试 13148307 text   （纯文本，只看解析结果）")
					return nil
				}

				textOnly := textModeKeywords[strings.ToLower(strings.TrimSpace(modeRaw))]
				sendReply(ctx, bot, fmt.Sprintf("正在拉取 UID %s 的动态...", uid))

				data, err := api.FetchSpaceFeed(ctx, uid, false)
				if err != nil {
					sendReply(ctx, bot, fmt.Sprintf("❌ 取数失败：%v\n%s", err, formatGlobalStatus(credMgr)))
					return nil
				}

				parsedList := ParseFeed(data)
				baselineStr := "未建立"
				if store.IsBaselineInitialized(uid) {
					baselineStr = "已建立"
				}

				header := []string{
					fmt.Sprintf("✅ 取数成功：UID %s 共 %d 条动态", uid, len(parsedList)),
					formatGlobalStatus(credMgr),
					"基线：" + baselineStr,
				}

				if len(parsedList) == 0 {
					header = append(header, "（该 UP 当前没有可解析的动态）")
					sendReply(ctx, bot, strings.Join(header, "\n"))
					return nil
				}

				previewHead := func(p ParsedDynamic) string {
					var flags []string
					if p.IsPinned {
						flags = append(flags, "置顶")
					}
					if p.IsDeletedSource {
						flags = append(flags, "源动态已删除")
					}
					if p.ParseDegraded {
						flags = append(flags, "解析降级")
					}
					catName := CategoryNames[p.Category]
					if catName == "" {
						catName = p.DynType
					}
					head := fmt.Sprintf("—— %s | %s", p.DynID, catName)
					if len(flags) > 0 {
						head += " | " + strings.Join(flags, "/")
					}
					if store.IsSeen(uid, p.DynID) {
						head += " | 已推送"
					}
					return head
				}

				if textOnly {
					header = append(header, "—— 模式：纯文本（解析预览）")
					start := len(parsedList) - 2
					if start < 0 {
						start = 0
					}
					tail := parsedList[start:]
					for i := len(tail) - 1; i >= 0; i-- {
						header = append(header, previewHead(tail[i]))
						header = append(header, truncateText(BuildText(tail[i], 300), 300))
					}
					sendReply(ctx, bot, strings.Join(header, "\n"))
					return nil
				}

				// Full render mode
				latest := parsedList[len(parsedList)-1]
				header = append(header, "—— 模式：完整渲染（与真实推送逐条一致）", previewHead(latest), "正在渲染，请稍候...")
				sendReply(ctx, bot, strings.Join(header, "\n"))

				segments, err := BuildMessages(ctx, app, api.httpClient, latest, credMgr.cfg.TextTruncateLength)
				if err != nil {
					logging.Module("bili_dyn_sub").WithError(err).Error("测试渲染失败")
					sendReply(ctx, bot, fmt.Sprintf("❌ 渲染失败：%v\n真实推送遇到同样情况会降级为纯文本（宁丑勿漏），文本内容如下：\n%s", err, truncateText(BuildText(latest, 300), 300)))
					return nil
				}

				var target SendTarget
				if bot.Event.GroupID > 0 {
					target = SendTarget{GroupID: bot.Event.GroupID}
				} else {
					target = SendTarget{UserID: bot.Event.UserID}
				}
				botID := bot.Event.SelfID
				infoCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
				loginResp := bot.CallActionWithContext(infoCtx, "get_login_info", nil)
				cancel()
				botNick := loginResp.Data.Get("nickname").String()
				if botNick == "" {
					botNick = "HakuBot"
				}
				sendInterval := time.Duration(credMgr.cfg.SendIntervalSeconds * float64(time.Second))

				if err := DispatchSegments(ctx, bot, botID, botNick, target, segments, credMgr.cfg.SendRetryTimes, sendInterval); err != nil {
					logging.Module("bili_dyn_sub").WithError(err).Warn("测试消息分发返回错误")
				}

				tailMsg := fmt.Sprintf("以上为 %d 个消息段的实际推送效果", len(segments))
				if len(segments) > 0 && segments[0].Type == "text" {
					tailMsg += "\n⚠️ 文字卡片渲染失败，已降级为纯文本（真实推送同此行为，请检查 Chromium/字体）"
				}
				sendReply(ctx, bot, tailMsg)
				return nil
			})
		})

	logging.Module("bili_dyn_sub").Info("B 站动态订阅命令已注册")
}
