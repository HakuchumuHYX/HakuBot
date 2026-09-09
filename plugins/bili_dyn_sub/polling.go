package bili_dyn_sub

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"math/big"
	"strconv"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/core"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/tidwall/gjson"
	zero "github.com/wdvxdr1123/ZeroBot"
	"github.com/wdvxdr1123/ZeroBot/message"
)

type PollingService struct {
	app                 *core.App
	cfg                 *Config
	store               *Store
	credMgr             *CredentialManager
	api                 *APIClient
	backoff             *BackoffManager
	mu                  sync.Mutex
	emptyFeedStreak     map[string]int
	lastForcedLoginTS   float64
	loginTransMu        sync.Mutex
	lastKnownLoginState *bool
}

func NewPollingService(
	app *core.App,
	cfg *Config,
	store *Store,
	credMgr *CredentialManager,
	api *APIClient,
	bm *BackoffManager,
) *PollingService {
	return &PollingService{
		app:             app,
		cfg:             cfg,
		store:           store,
		credMgr:         credMgr,
		api:             api,
		backoff:         bm,
		emptyFeedStreak: make(map[string]int),
	}
}

func (s *PollingService) Start(ctx context.Context) error {
	// 1. Background Polling with timer + jitter
	if err := s.app.Runtime.Go("bili_dyn_sub-poll", func(taskCtx context.Context) error {
		for {
			jitter := 0
			if s.cfg.PollJitterSeconds > 0 {
				n, _ := rand.Int(rand.Reader, big.NewInt(int64(s.cfg.PollJitterSeconds+1)))
				jitter = int(n.Int64())
			}
			delay := time.Duration(s.cfg.PollIntervalSeconds+jitter) * time.Second

			select {
			case <-taskCtx.Done():
				return taskCtx.Err()
			case <-time.After(delay):
			}

			if err := s.PollOnce(taskCtx); err != nil && !errors.Is(err, context.Canceled) {
				logging.Module("bili_dyn_sub").WithError(err).Error("轮询执行异常")
			}
		}
	}); err != nil {
		return err
	}

	// 2. Daily Prune at 04:10 local time
	if err := s.app.Runtime.Go("bili_dyn_sub-prune", func(taskCtx context.Context) error {
		for {
			now := time.Now()
			next := time.Date(now.Year(), now.Month(), now.Day(), 4, 10, 0, 0, now.Location())
			if !next.After(now) {
				next = next.AddDate(0, 0, 1)
			}
			waitDuration := next.Sub(now)

			select {
			case <-taskCtx.Done():
				return taskCtx.Err()
			case <-time.After(waitDuration):
			}

			s.store.Prune()
		}
	}); err != nil {
		return err
	}

	// 3. Login check (startup + every 6h) if sessdata is configured
	if s.cfg.SessData != "" {
		if err := s.app.Runtime.Go("bili_dyn_sub-login-check", func(taskCtx context.Context) error {
			// Startup verification after 30 seconds
			select {
			case <-taskCtx.Done():
				return taskCtx.Err()
			case <-time.After(30 * time.Second):
			}

			_, _ = s.CheckLoginStatus(taskCtx, false)

			ticker := time.NewTicker(6 * time.Hour)
			defer ticker.Stop()

			for {
				select {
				case <-taskCtx.Done():
					return taskCtx.Err()
				case <-ticker.C:
					_, _ = s.CheckLoginStatus(taskCtx, false)
				}
			}
		}); err != nil {
			return err
		}
	}

	return nil
}

func (s *PollingService) PollOnce(ctx context.Context) error {
	uids := s.store.GetAllUIDs()
	if len(uids) == 0 {
		logging.Module("bili_dyn_sub").Debug("暂无 B 站动态订阅，跳过本轮轮询")
		return nil
	}

	var activeBot *zero.Ctx
	var activeBotID int64
	zero.RangeBot(func(id int64, ctx *zero.Ctx) bool {
		activeBot = ctx
		activeBotID = id
		return false
	})

	if activeBot == nil {
		logging.Module("bili_dyn_sub").Info("当前没有可用的 Bot 连接，跳过本轮 B 站动态轮询")
		return nil
	}

	infoCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	loginResp := activeBot.CallActionWithContext(infoCtx, "get_login_info", nil)
	cancel()
	botNick := loginResp.Data.Get("nickname").String()
	if botNick == "" {
		botNick = "HakuBot"
	}
	gap := time.Duration(s.cfg.UIDRequestGapSeconds * float64(time.Second))

	for idx, uid := range uids {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if idx > 0 && gap > 0 {
			if err := network.Wait(ctx, gap); err != nil {
				return err
			}
		}

		s.pollUID(ctx, activeBot, activeBotID, botNick, uid)
	}

	return nil
}

func (s *PollingService) pollUID(ctx context.Context, bot *zero.Ctx, botID int64, botNick, uid string) {
	if s.backoff.IsBackingOff(uid, time.Now()) {
		rem := s.backoff.RemainingSeconds(uid, time.Now())
		logging.Module("bili_dyn_sub").Debugf("UID %s 仍在退避中（剩余 %ds），跳过本轮", uid, rem)
		return
	}

	forceRefresh := false
	var feedData gjson.Result
	var fetchErr error

	for attempt := 1; attempt <= 2; attempt++ {
		data, err := s.api.FetchSpaceFeed(ctx, uid, forceRefresh)
		if err == nil {
			feedData = data
			fetchErr = nil
			break
		}
		fetchErr = err

		var riskErr *BiliRiskControlError
		if errors.As(err, &riskErr) {
			action := s.backoff.OnRiskControl(uid, "-352", time.Now())
			if action == ActionRefreshCookie && attempt == 1 {
				logging.Module("bili_dyn_sub").Infof("UID %s 触发 -352 风控，强制重造 cookie 后重试一次", uid)
				forceRefresh = true
				continue
			}
			rem := s.backoff.RemainingSeconds(uid, time.Now())
			if s.backoff.ShouldLogWarning(uid, "-352") {
				logging.Module("bili_dyn_sub").Warnf("UID %s 持续被 -352 风控，退避 %ds；长期不恢复请在 config.json 配置 sessdata", uid, rem)
			}
			return
		}

		var ipErr *BiliIpBlockedError
		if errors.As(err, &ipErr) {
			s.backoff.OnIPBlock(uid, "HTTP 412", time.Now())
			if s.backoff.ShouldLogWarning(uid, "412") {
				rem := s.backoff.RemainingSeconds(uid, time.Now())
				logging.Module("bili_dyn_sub").Warnf("UID %s 命中 IP 层风控（HTTP 412），退避 %ds；请配置 proxy", uid, rem)
			}
			return
		}

		var captchaErr *BiliCaptchaError
		if errors.As(err, &captchaErr) {
			if s.backoff.ShouldLogWarning(uid, "captcha") {
				logging.Module("bili_dyn_sub").Warnf("UID %s 需要人机验证，本轮放弃；建议配置 sessdata", uid)
			}
			return
		}

		var authErr *BiliAuthError
		if errors.As(err, &authErr) {
			if s.backoff.ShouldLogWarning(uid, "-101") {
				logging.Module("bili_dyn_sub").Warnf("UID %s 取数返回 -101，正在确认真实登录态", uid)
			}
			s.confirmLoginAfterAuthError(ctx)
			return
		}

		var signErr *BiliSignError
		if errors.As(err, &signErr) {
			s.api.InvalidateWbiKeys()
			if attempt == 1 {
				logging.Module("bili_dyn_sub").Infof("UID %s wbi 签名被拒，作废 key 缓存后重试一次", uid)
				continue
			}
			if s.backoff.ShouldLogWarning(uid, "-403") {
				logging.Module("bili_dyn_sub").Warnf("UID %s 刷新 wbi key 后仍签名失败，放弃本轮", uid)
			}
			return
		}

		var netErr *BiliNetworkError
		if errors.As(err, &netErr) {
			s.backoff.OnNetworkError(uid, err.Error(), time.Now())
			if s.backoff.ShouldLogWarning(uid, "network") {
				logging.Module("bili_dyn_sub").Warnf("UID %s 取数网络异常，退避 60s: %v", uid, err)
			}
			return
		}

		// Other BiliApiError
		if s.backoff.ShouldLogWarning(uid, fmt.Sprintf("api:%v", err)) {
			logging.Module("bili_dyn_sub").Warnf("UID %s 取数失败: %v", uid, err)
		}
		return
	}

	if fetchErr != nil || !feedData.Exists() {
		return
	}

	s.backoff.OnSuccess(uid)
	s.store.TouchLastSuccess(uid, false)

	parsedList := ParseFeed(feedData)

	s.mu.Lock()
	if len(parsedList) > 0 {
		delete(s.emptyFeedStreak, uid)
	} else {
		streak := s.emptyFeedStreak[uid] + 1
		s.emptyFeedStreak[uid] = streak
		if s.store.IsBaselineInitialized(uid) {
			if streak%5 == 0 {
				logging.Module("bili_dyn_sub").Warnf("UID %s 已连续 %d 轮取数成功但 feed 为空，疑似软风控", uid, streak)
			} else {
				logging.Module("bili_dyn_sub").Debugf("UID %s 已连续 %d 轮取数成功但 feed 为空", uid, streak)
			}
		}
	}
	streak := s.emptyFeedStreak[uid]
	s.mu.Unlock()

	// 1. Baseline initialization
	if !s.store.IsBaselineInitialized(uid) {
		if len(parsedList) == 0 {
			if streak < 3 {
				logging.Module("bili_dyn_sub").Infof("UID %s 取数成功但无动态（第 %d/3 次），暂不建立基线", uid, streak)
				return
			}
			s.store.InitBaseline(uid, []string{}, big.NewInt(1))
			s.mu.Lock()
			delete(s.emptyFeedStreak, uid)
			s.mu.Unlock()
			logging.Module("bili_dyn_sub").Infof("UID %s 连续 3 轮均无历史动态，已建立哨兵基线", uid)
			return
		}

		var ids []string
		for _, p := range parsedList {
			if p.DynID != "" {
				ids = append(ids, p.DynID)
			}
		}
		s.store.InitBaseline(uid, ids, big.NewInt(0))
		logging.Module("bili_dyn_sub").Infof("UID %s 首次建立基线：%d 条历史动态标记已读，本轮不推送", uid, len(ids))
		return
	}

	// 2. Select pushable items using snapshot of seen
	var fresh []ParsedDynamic
	for _, p := range parsedList {
		if p.DynID != "" && !s.store.IsSeen(uid, p.DynID) {
			fresh = append(fresh, p)
		}
	}
	if len(fresh) == 0 {
		if err := s.store.Save(); err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Errorf("UID %s 成功取数更新 last_success 落盘失败: %v", uid, err)
		}
		return
	}

	categories := make(map[int]bool)
	for _, c := range s.store.GetCategories(uid) {
		categories[c] = true
	}

	var candidates []ParsedDynamic
	for _, p := range fresh {
		if ShouldSkip(p) {
			s.store.MarkSeen(uid, p.DynID, false)
			continue
		}
		if p.Category != 0 && !categories[p.Category] {
			s.store.MarkSeen(uid, p.DynID, false)
			continue
		}
		candidates = append(candidates, p)
	}

	stale := 0
	overflow := 0

	// Check max_dynamic_age_minutes
	if s.cfg.MaxDynamicAgeMinutes > 0 && len(candidates) > 0 {
		deadline := time.Now().Unix() - int64(s.cfg.MaxDynamicAgeMinutes*60)
		var kept []ParsedDynamic
		for _, p := range candidates {
			if p.PubTS > 0 && p.PubTS < deadline {
				logging.Module("bili_dyn_sub").Infof("UID %s 动态 %s 超出推送窗口，仅标记已读", uid, p.DynID)
				s.store.MarkSeen(uid, p.DynID, false)
				stale++
			} else {
				kept = append(kept, p)
			}
		}
		candidates = kept
	}

	// Check max_push_per_round
	if s.cfg.MaxPushPerRound > 0 && len(candidates) > s.cfg.MaxPushPerRound {
		excess := candidates[:len(candidates)-s.cfg.MaxPushPerRound]
		candidates = candidates[len(candidates)-s.cfg.MaxPushPerRound:]
		for _, p := range excess {
			s.store.MarkSeen(uid, p.DynID, false)
		}
		overflow = len(excess)
		logging.Module("bili_dyn_sub").Infof("UID %s 新动态超过单轮上限，较旧的 %d 条仅标记已读", uid, overflow)
	}

	if err := s.store.Save(); err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Errorf("UID %s 过滤新动态后持久化 state.json 失败: %v", uid, err)
	}

	if len(candidates) == 0 {
		return
	}

	// Subscriptions are the sole switch
	targets := s.store.GetGroups(uid)
	if len(targets) == 0 {
		for _, p := range candidates {
			s.store.MarkSeen(uid, p.DynID, false)
		}
		if err := s.store.Save(); err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Errorf("UID %s 标记新动态已读后持久化 state.json 失败（仅内存生效）", uid)
		}
		logging.Module("bili_dyn_sub").Infof("UID %s 有 %d 条新动态，但无订阅群，仅标记已读", uid, len(candidates))
		return
	}

	logging.Module("bili_dyn_sub").Infof("UID %s 发现 %d 条新动态，推送到 %d 个群", uid, len(candidates), len(targets))

	sendInterval := time.Duration(s.cfg.SendIntervalSeconds * float64(time.Second))
	for _, p := range candidates {
		// Mark seen and persist first before sending
		s.store.MarkSeen(uid, p.DynID, false)
		if err := s.store.Save(); err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Errorf("UID %s 推送前持久化已读状态失败（仅内存已读，seen 未持久化，若此时崩溃重启可能重复推送）: 动态 ID %s", uid, p.DynID)
		}

		segments, err := BuildMessages(ctx, s.app, s.api.httpClient, p, s.cfg.TextTruncateLength)
		if err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Errorf("渲染动态 %s 消息段失败", p.DynID)
			continue
		}

		for _, groupID := range targets {
			if ctx.Err() != nil {
				return
			}
			target := SendTarget{GroupID: groupID}
			if err := DispatchSegments(ctx, bot, botID, botNick, target, segments, s.cfg.SendRetryTimes, sendInterval); err != nil {
				logging.Module("bili_dyn_sub").WithError(err).Warnf("向群 %d 推送动态 %s 失败", groupID, p.DynID)
			}
		}
	}

	if overflow > 0 {
		tip := message.Message{message.Text(fmt.Sprintf("另有 %d 条动态未展示", overflow))}
		for _, groupID := range targets {
			sendWithRetry(ctx, bot, SendTarget{GroupID: groupID}, tip, s.cfg.SendRetryTimes, sendInterval, "未展示提示")
		}
	}
	if stale > 0 {
		logging.Module("bili_dyn_sub").Infof("UID %s 另有 %d 条动态超出推送窗口，已静默标记已读", uid, stale)
	}
}

func (s *PollingService) confirmLoginAfterAuthError(ctx context.Context) {
	now := float64(time.Now().UnixNano()) / 1e9
	s.mu.Lock()
	force := now-s.lastForcedLoginTS >= 600.0
	if force {
		s.lastForcedLoginTS = now
	}
	s.mu.Unlock()

	_, _ = s.CheckLoginStatus(ctx, force)
}

func (s *PollingService) CheckLoginStatus(ctx context.Context, force bool) (LoginStatus, error) {
	status, err := s.credMgr.VerifyLogin(ctx, force)
	if err != nil {
		return status, err
	}
	s.handleLoginTransition(ctx, status)
	return status, nil
}

func (s *PollingService) handleLoginTransition(ctx context.Context, status LoginStatus) {
	s.loginTransMu.Lock()
	defer s.loginTransMu.Unlock()

	if !status.Configured {
		s.lastKnownLoginState = nil
		return
	}
	if status.Error != "" {
		return
	}

	if status.IsLogin {
		if s.lastKnownLoginState != nil && !*s.lastKnownLoginState {
			logging.Module("bili_dyn_sub").Infof("B 站登录态已恢复（uname=%s），无需再提醒超管", status.UName)
		}
		t := true
		s.lastKnownLoginState = &t
		return
	}

	// Confirmed invalid
	if s.lastKnownLoginState != nil && !*s.lastKnownLoginState {
		logging.Module("bili_dyn_sub").Debug("B 站登录态仍处于失效状态，已提醒过超管，不重复打扰")
		return
	}

	logging.Module("bili_dyn_sub").Warn("B 站登录态失效：已退化为匿名请求，正在私聊提醒超管")
	if s.notifySuperusersLoginExpired(ctx, status) {
		f := false
		s.lastKnownLoginState = &f
	}
}

func (s *PollingService) notifySuperusersLoginExpired(ctx context.Context, status LoginStatus) bool {
	superusers := s.app.Access.Superusers()
	if len(superusers) == 0 {
		logging.Module("bili_dyn_sub").Warn("B 站登录态已失效，但未配置 superusers，无法私聊提醒")
		return true
	}

	var activeBot *zero.Ctx
	zero.RangeBot(func(id int64, c *zero.Ctx) bool {
		activeBot = c
		return false
	})

	if activeBot == nil {
		logging.Module("bili_dyn_sub").Warn("登录态失效提醒暂时无法发送（无可用 Bot 连接，将在连接后重试）")
		return false
	}

	text := fmt.Sprintf("【B站动态订阅】登录态失效\n状态：%s\n已自动退化为匿名取数（功能不中断，但风控概率上升，可能出现 -352 空轮）。\n请重新获取小号的 SESSDATA 并填入 config/plugins/bili_dyn_sub/config.json 的 sessdata 字段后重启 bot。", status.Summary())
	msg := message.Message{message.Text(text)}
	sendInterval := time.Duration(s.cfg.SendIntervalSeconds * float64(time.Second))

	for _, su := range superusers {
		uid, err := strconv.ParseInt(su, 10, 64)
		if err != nil || uid <= 0 {
			continue
		}
		sendWithRetry(ctx, activeBot, SendTarget{UserID: uid}, msg, s.cfg.SendRetryTimes, sendInterval, "登录失效提醒")
	}
	return true
}
