package analysis_bilibili

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

const pluginID = "analysis_bilibili"

type dedupKey struct {
	BotID    int64
	ChatType string
	ChatID   int64
	URL      string
}

type dedupEntry struct {
	sending   bool
	expiresAt time.Time
}

type dedupCache struct {
	mu      sync.Mutex
	ttl     time.Duration
	entries map[dedupKey]*dedupEntry
}

func newDedupCache(ttlSeconds float64) *dedupCache {
	return &dedupCache{
		ttl:     time.Duration(ttlSeconds * float64(time.Second)),
		entries: make(map[dedupKey]*dedupEntry),
	}
}

func (c *dedupCache) TryAcquire(key dedupKey) bool {
	if c.ttl <= 0 {
		return true
	}
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()
	for k, v := range c.entries {
		if !v.sending && now.After(v.expiresAt) {
			delete(c.entries, k)
		}
	}

	if entry, ok := c.entries[key]; ok {
		if entry.sending || now.Before(entry.expiresAt) {
			return false
		}
	}

	c.entries[key] = &dedupEntry{
		sending:   true,
		expiresAt: now.Add(c.ttl),
	}
	return true
}

func (c *dedupCache) Complete(key dedupKey) {
	if c.ttl <= 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if entry, ok := c.entries[key]; ok {
		entry.sending = false
		entry.expiresAt = time.Now().Add(c.ttl)
	}
}

func (c *dedupCache) Failed(key dedupKey) {
	if c.ttl <= 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.entries, key)
}

func isAllowed(app *core.App, cfg Config, groupID, userID int64) bool {
	userStr := strconv.FormatInt(userID, 10)
	groupStr := ""
	if groupID != 0 {
		groupStr = strconv.FormatInt(groupID, 10)
		if !app.Access.Enabled(pluginID, groupStr, userStr) {
			return false
		}
	}

	userInWhitelist := slices.Contains(cfg.AnalysisWhitelist, userStr)
	groupInWhitelist := groupID != 0 && slices.Contains(cfg.AnalysisGroupWhitelist, groupStr)
	if userInWhitelist || groupInWhitelist {
		return true
	}

	if len(cfg.AnalysisWhitelist) > 0 || len(cfg.AnalysisGroupWhitelist) > 0 {
		return false
	}

	userInBlacklist := slices.Contains(cfg.AnalysisBlacklist, userStr)
	groupInBlacklist := groupID != 0 && slices.Contains(cfg.AnalysisGroupBlacklist, groupStr)
	if userInBlacklist || groupInBlacklist {
		return false
	}

	return true
}

func plainText(msg message.Message) string {
	var sb strings.Builder
	for _, seg := range msg {
		if seg.Type == "text" {
			sb.WriteString(seg.Data["text"])
		}
	}
	return sb.String()
}

func isBusinessError(err error) (string, bool) {
	for e := err; e != nil; e = errors.Unwrap(e) {
		msg := e.Error()
		switch msg {
		case "B站风控校验中，请稍后再试",
			"解析到视频被删了/稿件不可见或审核中/权限不足",
			"番剧信息获取失败",
			"直播间信息获取失败",
			"专栏信息获取失败",
			"动态信息获取失败（可能被风控）",
			"动态内容为空",
			"未找到视频",
			"请提供视频关键词":
			return msg, true
		}
	}
	return "", false
}

func userErrorMessage(err error) string {
	if errors.Is(err, ErrHTTP412) {
		return "B站风控校验中，请稍后再试"
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return "请求超时，请稍后再试"
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return "网络请求超时，请稍后再试"
	}
	if bMsg, ok := isBusinessError(err); ok {
		return bMsg
	}
	return "解析失败，请稍后再试"
}

func isSearchCommand(msg message.Message) bool {
	text := plainText(msg)
	return strings.HasPrefix(strings.TrimSpace(text), "搜视频")
}

func sendOneBotMessage(ctx context.Context, bot *zero.Ctx, groupID, userID int64, msg message.Message) (string, int64, string) {
	action := "send_group_msg"
	params := zero.Params{"group_id": groupID, "message": msg}
	if groupID == 0 {
		action = "send_private_msg"
		params = zero.Params{"user_id": userID, "message": msg}
	}
	rsp := bot.CallActionWithContext(ctx, action, params)
	return rsp.Status, rsp.RetCode, rsp.Message
}

func sendText(ctx context.Context, bot *zero.Ctx, text string) {
	if errors.Is(ctx.Err(), context.Canceled) || text == "" {
		return
	}
	sendCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	sendOneBotMessage(sendCtx, bot, bot.Event.GroupID, bot.Event.UserID, message.Message{message.Text(text)})
}

func sendWithTextFallback(parentCtx context.Context, bot *zero.Ctx, groupID, userID int64, msg message.Message) (string, int64, string) {
	sendCtx, cancel := context.WithTimeout(parentCtx, 30*time.Second)
	defer cancel()

	hasImage := false
	for _, seg := range msg {
		if seg.Type == "image" {
			hasImage = true
			break
		}
	}

	status, retCode, msgErr := sendOneBotMessage(sendCtx, bot, groupID, userID, msg)
	if status == "" || errors.Is(sendCtx.Err(), context.DeadlineExceeded) {
		return "", retCode, "发送超时或结果未知"
	}
	if (status == "ok" && retCode == 0) || status == "async" || retCode == 1 {
		return status, retCode, msgErr
	}

	if hasImage {
		var plainMsg message.Message
		for _, seg := range msg {
			if seg.Type == "text" {
				plainMsg = append(plainMsg, seg)
			}
		}
		if len(plainMsg) > 0 {
			logging.Event(pluginID, bot.Event).Warn("图片消息被 OneBot 明确拒绝，尝试纯文字降级发送")
			status2, retCode2, msgErr2 := sendOneBotMessage(sendCtx, bot, groupID, userID, plainMsg)
			if status2 == "" || errors.Is(sendCtx.Err(), context.DeadlineExceeded) {
				return "", retCode2, "纯文字降级发送超时或结果未知"
			}
			return status2, retCode2, msgErr2
		}
	}
	return status, retCode, msgErr
}

func sendDedupResult(ctx context.Context, bot *zero.Ctx, dedup *dedupCache, res result) {
	if len(res.Message) == 0 {
		return
	}
	chatType := "group"
	chatID := bot.Event.GroupID
	if bot.Event.GroupID == 0 {
		chatType = "private"
		chatID = bot.Event.UserID
	}
	key := dedupKey{
		BotID:    bot.Event.SelfID,
		ChatType: chatType,
		ChatID:   chatID,
		URL:      res.URL,
	}

	if !dedup.TryAcquire(key) {
		return
	}

	status, retCode, sendErr := sendWithTextFallback(ctx, bot, bot.Event.GroupID, bot.Event.UserID, res.Message)
	if (status == "ok" && retCode == 0) || status == "async" || retCode == 1 || status == "" {
		dedup.Complete(key)
	} else {
		logging.Event(pluginID, bot.Event).Errorf("发送消息失败 (%d): %s", retCode, sendErr)
		dedup.Failed(key)
	}
}

func fetchDetail(ctx context.Context, client *http.Client, s *signer, cfg Config, tgt *target, descBlacklisted bool) (result, error) {
	switch tgt.Kind {
	case "video":
		return fetchVideoDetail(ctx, client, s, cfg, tgt, descBlacklisted)
	case "bangumi_ep", "bangumi_ss", "bangumi_md":
		return fetchBangumiDetail(ctx, client, cfg, tgt, descBlacklisted)
	case "live":
		return fetchLiveDetail(ctx, client, cfg, tgt)
	case "article":
		return fetchArticleDetail(ctx, client, cfg, tgt)
	case "dynamic":
		return fetchDynamicDetail(ctx, client, cfg, tgt)
	default:
		return result{}, fmt.Errorf("不支持的内容类型: %s", tgt.Kind)
	}
}

func Register(app *core.App) error {
	paths, err := app.Paths.Plugin(pluginID)
	if err != nil {
		return err
	}
	cfg, err := loadConfig(filepath.Join(paths.Config, "config.json"))
	if err != nil {
		return err
	}

	proxy := "direct"
	if cfg.AnalysisTrustEnv {
		proxy = ""
	}
	client, err := network.NewClient(proxy, 20*time.Second, true)
	if err != nil {
		return err
	}
	if tr, ok := client.Transport.(*http.Transport); ok {
		tr.DialContext = (&net.Dialer{
			Timeout:   8 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext
	}

	if err = app.Runtime.AddCloser(func() error {
		client.CloseIdleConnections()
		return nil
	}); err != nil {
		return err
	}

	s := newSigner()
	dedup := newDedupCache(cfg.AnalysisReanalysisTime)
	engine := zero.New()

	engine.OnMessage().SetPriority(11).SetBlock(false).Handle(func(bot *zero.Ctx) {
		if !isAllowed(app, cfg, bot.Event.GroupID, bot.Event.UserID) {
			return
		}

		if cfg.AnalysisUseOnMessage {
			for _, seg := range bot.Event.Message {
				if seg.Type == "forward" {
					logging.Event(pluginID, bot.Event).Debug("analysis_bilibili 忽略转发消息")
					return
				}
			}
		}

		if cfg.AnalysisEnableSearch && isSearchCommand(bot.Event.Message) {
			return
		}

		bot.NoTimeout()
		_ = app.Runtime.Do(func(ctx context.Context) error {
			tgt, searchTitle, err := extractTarget(ctx, client, bot)
			if err != nil {
				if errors.Is(err, context.Canceled) || ctx.Err() != nil {
					return nil
				}
				logging.Event(pluginID, bot.Event).WithError(err).Warn("提取目标失败")
				sendText(ctx, bot, userErrorMessage(err))
				return nil
			}

			// 普通消息未命中 B 站内容直接返回，不触发凭据网络请求
			if tgt == nil && searchTitle == "" {
				return nil
			}

			if tgt == nil && searchTitle != "" {
				arcURL, err := searchVideoByTitle(ctx, client, s, searchTitle)
				if err != nil {
					if errors.Is(err, context.Canceled) || ctx.Err() != nil {
						return nil
					}
					logging.Event(pluginID, bot.Event).WithError(err).Warnf("卡片标题搜索 %q 失败", searchTitle)
					sendText(ctx, bot, userErrorMessage(err))
					return nil
				}
				tgt = extractTargetFromText(arcURL)
				if tgt == nil {
					logging.Event(pluginID, bot.Event).Warnf("卡片标题搜索结果链接无法识别: %s", arcURL)
					sendText(ctx, bot, "解析失败，请稍后再试")
					return nil
				}
			}

			if tgt == nil {
				return nil
			}

			s.ensureTicket(ctx, client)

			groupStr := ""
			if bot.Event.GroupID != 0 {
				groupStr = strconv.FormatInt(bot.Event.GroupID, 10)
			}
			descBlacklisted := groupStr != "" && slices.Contains(cfg.AnalysisDescBlacklist, groupStr)

			res, err := fetchDetail(ctx, client, s, cfg, tgt, descBlacklisted)
			if err != nil {
				if errors.Is(err, context.Canceled) || ctx.Err() != nil {
					return nil
				}
				logging.Event(pluginID, bot.Event).WithError(err).Warn("解析 B 站详情失败")
				sendText(ctx, bot, userErrorMessage(err))
				return nil
			}

			sendDedupResult(ctx, bot, dedup, res)
			return nil
		})
	})

	if cfg.AnalysisEnableSearch {
		engine.OnMessage(func(ctx *zero.Ctx) bool {
			return isSearchCommand(ctx.Event.Message)
		}).SetPriority(1).SetBlock(false).Handle(func(bot *zero.Ctx) {
			if !isAllowed(app, cfg, bot.Event.GroupID, bot.Event.UserID) {
				return
			}
			rawText := plainText(bot.Event.Message)
			trimmed := strings.TrimSpace(rawText)
			keyword := strings.TrimSpace(strings.TrimPrefix(trimmed, "搜视频"))

			bot.NoTimeout()
			_ = app.Runtime.Do(func(ctx context.Context) error {
				if keyword == "" {
					sendText(ctx, bot, "请提供视频关键词")
					return nil
				}

				arcURL, err := searchVideoByTitle(ctx, client, s, keyword)
				if err != nil {
					if errors.Is(err, context.Canceled) || ctx.Err() != nil {
						return nil
					}
					logging.Event(pluginID, bot.Event).WithError(err).Warn("搜视频失败")
					sendText(ctx, bot, userErrorMessage(err))
					return nil
				}
				tgt := extractTargetFromText(arcURL)
				if tgt == nil {
					sendText(ctx, bot, "未找到视频")
					return nil
				}

				groupStr := ""
				if bot.Event.GroupID != 0 {
					groupStr = strconv.FormatInt(bot.Event.GroupID, 10)
				}
				descBlacklisted := groupStr != "" && slices.Contains(cfg.AnalysisDescBlacklist, groupStr)

				res, err := fetchVideoDetail(ctx, client, s, cfg, tgt, descBlacklisted)
				if err != nil {
					if errors.Is(err, context.Canceled) || ctx.Err() != nil {
						return nil
					}
					logging.Event(pluginID, bot.Event).WithError(err).Warn("获取搜索视频详情失败")
					sendText(ctx, bot, userErrorMessage(err))
					return nil
				}

				sendDedupResult(ctx, bot, dedup, res)
				return nil
			})
		})
	}

	logging.Module(pluginID).Info("B 站链接解析插件已注册")
	return nil
}
