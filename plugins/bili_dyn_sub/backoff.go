package bili_dyn_sub

import (
	"math"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

const (
	MaxRefreshCount           = 2
	RiskBackoffBaseSeconds    = 300.0
	RiskBackoffMaxSeconds     = 3600.0
	IPBlockBackoffBaseSeconds = 1800.0
	IPBlockBackoffMaxSeconds  = 14400.0
	NetworkBackoffSeconds     = 60.0
	ErrorIPBlock              = "ip_block"
	ErrorNetworkPrefix        = "network:"
)

type RiskAction string

const (
	ActionRefreshCookie RiskAction = "refresh_cookie"
	ActionBackoff       RiskAction = "backoff"
)

type BackoffState struct {
	FailCount    int
	RefreshCount int
	BackoffUntil *time.Time
	LastError    string
	LastLogKey   string
}

type BackoffManager struct {
	mu     sync.Mutex
	states map[string]*BackoffState
}

func NewBackoffManager() *BackoffManager {
	return &BackoffManager{
		states: make(map[string]*BackoffState),
	}
}

func (bm *BackoffManager) state(uid string) *BackoffState {
	key := strings.TrimSpace(uid)
	s := bm.states[key]
	if s == nil {
		s = &BackoffState{}
		bm.states[key] = s
	}
	return s
}

func (bm *BackoffManager) enterBackoff(s *BackoffState, delaySeconds float64, now time.Time) {
	until := now.Add(time.Duration(delaySeconds * float64(time.Second)))
	if s.BackoffUntil == nil || until.After(*s.BackoffUntil) {
		s.BackoffUntil = &until
	}
}

func (bm *BackoffManager) IsBackingOff(uid string, now time.Time) bool {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.states[key]
	if s == nil || s.BackoffUntil == nil {
		return false
	}
	if now.Before(*s.BackoffUntil) {
		return true
	}
	s.BackoffUntil = nil
	return false
}

func (bm *BackoffManager) RemainingSeconds(uid string, now time.Time) int {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.states[key]
	if s == nil || s.BackoffUntil == nil {
		return 0
	}
	rem := s.BackoffUntil.Sub(now).Seconds()
	if rem <= 0 {
		return 0
	}
	return int(math.Ceil(rem))
}

func (bm *BackoffManager) OnSuccess(uid string) {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.states[key]
	if s == nil {
		return
	}
	if s.FailCount > 0 || s.RefreshCount > 0 || s.BackoffUntil != nil || s.LastError != "" {
		logging.Module("bili_dyn_sub").Infof("UID %s 取数恢复正常（此前连续失败 %d 次，最近错误: %s）", key, s.FailCount, s.LastError)
	}
	delete(bm.states, key)
}

func (bm *BackoffManager) OnRiskControl(uid string, reason string, now time.Time) RiskAction {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.state(key)
	s.FailCount++
	s.LastError = reason
	if s.RefreshCount < MaxRefreshCount {
		s.RefreshCount++
		logging.Module("bili_dyn_sub").Debugf("UID %s 触发风控（%s），强制刷新 cookie 重试 %d/%d", key, reason, s.RefreshCount, MaxRefreshCount)
		return ActionRefreshCookie
	}
	step := s.FailCount - MaxRefreshCount - 1
	if step < 0 {
		step = 0
	}
	multiplier := math.Pow(2, float64(min(step, 30)))
	delay := math.Min(RiskBackoffBaseSeconds*multiplier, RiskBackoffMaxSeconds)
	bm.enterBackoff(s, delay, now)
	logging.Module("bili_dyn_sub").Debugf("UID %s 刷新 cookie 仍风控（%s），退避 %ds", key, reason, int(delay))
	return ActionBackoff
}

func (bm *BackoffManager) OnIPBlock(uid string, reason string, now time.Time) {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.state(key)
	s.FailCount++
	s.LastError = ErrorIPBlock
	step := s.FailCount - 1
	if step < 0 {
		step = 0
	}
	multiplier := math.Pow(2, float64(min(step, 30)))
	delay := math.Min(IPBlockBackoffBaseSeconds*multiplier, IPBlockBackoffMaxSeconds)
	bm.enterBackoff(s, delay, now)
	logging.Module("bili_dyn_sub").Debugf("UID %s 命中 IP 层风控（%s），退避 %ds", key, reason, int(delay))
}

func (bm *BackoffManager) OnNetworkError(uid string, reason string, now time.Time) {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.state(key)
	s.LastError = ErrorNetworkPrefix + reason
	bm.enterBackoff(s, NetworkBackoffSeconds, now)
	logging.Module("bili_dyn_sub").Debugf("UID %s 网络错误（%s），退避 %ds", key, reason, int(NetworkBackoffSeconds))
}

func (bm *BackoffManager) ShouldLogWarning(uid string, errorKey string) bool {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	key := strings.TrimSpace(uid)
	s := bm.state(key)
	normKey := strings.TrimSpace(errorKey)
	if normKey == s.LastLogKey {
		return false
	}
	s.LastLogKey = normKey
	return true
}

func (bm *BackoffManager) Forget(uid string) {
	bm.mu.Lock()
	defer bm.mu.Unlock()
	delete(bm.states, strings.TrimSpace(uid))
}
