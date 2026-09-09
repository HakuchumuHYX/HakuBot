package bili_dyn_sub

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/bilibili"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/rendering"
	cdpnetwork "github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/storage"
	"github.com/chromedp/chromedp"
	"github.com/tidwall/gjson"
)

const (
	SPIURL              = "https://api.bilibili.com/x/frontend/finger/spi"
	ExClimbURL          = "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi"
	TicketURL           = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
	NavURL              = "https://api.bilibili.com/x/web-interface/nav"
	SpaceURLTemplate    = "https://space.bilibili.com/%s/dynamic"
	WWWReferer          = "https://www.bilibili.com/"
	SpaceOrigin         = "https://space.bilibili.com"
	BuvidTTLSeconds     = 365 * 24 * 3600.0
	TicketTTLSeconds    = 3 * 24 * 3600.0
	DegradedTTLSeconds  = 600.0
	FailedRetrySeconds  = 300.0
	MinRefreshInterval  = 60.0
	SaveThrottleSeconds = 300.0
	ForcedStreakReset   = 1800.0
	LoginStatusTTL      = 1800.0
	LoginErrorRetryTTL  = 300.0
)

var mutableCookieKeys = map[string]bool{
	"buvid3":              true,
	"buvid4":              true,
	"b_nut":               true,
	"buvid_fp":            true,
	"_uuid":               true,
	"b_lsid":              true,
	"bili_ticket":         true,
	"bili_ticket_expires": true,
	"sid":                 true,
}

func rotl64(x uint64, r uint) uint64 {
	return (x << r) | (x >> (64 - r))
}

func fmix64(k uint64) uint64 {
	k ^= k >> 33
	k *= 0xff51afd7ed558ccd
	k ^= k >> 33
	k *= 0xc4ceb9fe1a85ec53
	k ^= k >> 33
	return k
}

// MurmurHash3_x64_128 implements MurmurHash3 x64_128 matching Python's pure implementation.
func MurmurHash3_x64_128(data []byte, seed uint32) (uint64, uint64) {
	const (
		c1 = uint64(0x87c37b91114253d5)
		c2 = uint64(0x4cf5ad432745937f)
	)

	h1 := uint64(seed)
	h2 := uint64(seed)

	nblocks := len(data) / 16
	for i := 0; i < nblocks; i++ {
		base := i * 16
		k1 := binary.LittleEndian.Uint64(data[base : base+8])
		k2 := binary.LittleEndian.Uint64(data[base+8 : base+16])

		k1 *= c1
		k1 = rotl64(k1, 31)
		k1 *= c2
		h1 ^= k1

		h1 = rotl64(h1, 27)
		h1 += h2
		h1 = h1*5 + 0x52dce729

		k2 *= c2
		k2 = rotl64(k2, 33)
		k2 *= c1
		h2 ^= k2

		h2 = rotl64(h2, 31)
		h2 += h1
		h2 = h2*5 + 0x38495ab5
	}

	tail := data[nblocks*16:]
	var k1, k2 uint64
	for i, b := range tail {
		if i < 8 {
			k1 |= uint64(b) << (8 * i)
		} else {
			k2 |= uint64(b) << (8 * (i - 8))
		}
	}

	if len(tail) > 8 {
		k2 *= c2
		k2 = rotl64(k2, 33)
		k2 *= c1
		h2 ^= k2
	}
	if len(tail) > 0 {
		k1 *= c1
		k1 = rotl64(k1, 31)
		k1 *= c2
		h1 ^= k1
	}

	length := uint64(len(data))
	h1 ^= length
	h2 ^= length

	h1 += h2
	h2 += h1

	h1 = fmix64(h1)
	h2 = fmix64(h2)

	h1 += h2
	h2 += h1

	return h1, h2
}

func genBuvidFp(payload string) string {
	h1, h2 := MurmurHash3_x64_128([]byte(payload), 31)
	return fmt.Sprintf("%016x%016x", h1, h2)
}

func randomHex(length int) string {
	b := make([]byte, (length+1)/2)
	rand.Read(b)
	s := strings.ToUpper(hex.EncodeToString(b))
	if len(s) > length {
		return s[:length]
	}
	return s
}

func genUUIDInfoc() string {
	p1 := randomHex(8)
	p2 := randomHex(4)
	p3 := randomHex(4)
	p4 := randomHex(4)
	p5 := randomHex(12)
	ms := time.Now().UnixNano() / int64(time.Millisecond)
	mod := ms % 100000
	modStr := fmt.Sprintf("%05d", mod)
	return fmt.Sprintf("%s-%s-%s-%s-%s%sinfoc", p1, p2, p3, p4, p5, modStr)
}

func genBLSID() string {
	prefix := randomHex(8)
	ms := time.Now().UnixNano() / int64(time.Millisecond)
	return fmt.Sprintf("%s_%X", prefix, ms)
}

func buildActivatePayload(uuid, ua string) string {
	innerEnv := map[string]any{
		"2673": 0, "5766": 24, "6527": 0, "7003": 1, "807e": 1,
		"b8ce": ua,
		"641c": 0, "07a4": "zh-CN", "1c57": "not available", "0bd0": 16,
		"748e": []int{1920, 1080}, "d61f": []int{1920, 1032},
		"fc9d": -480, "6aa9": "Asia/Shanghai",
		"75b8": 1, "3b21": 1, "8a1c": 0, "d52f": "not available", "adca": "Win32",
		"80c9": []any{}, "13ab": "", "bfe9": "", "a3c1": []any{},
		"6bc5": "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
		"ed31": 0, "72bd": 0, "097b": 0, "52cd": []int{0, 0, 0}, "a658": []any{},
		"d02f": "124.04347527516074",
	}
	content := map[string]any{
		"3064": 1, "5062": strconv.FormatInt(time.Now().UnixNano()/1e6, 10), "03bf": WWWReferer,
		"39c8": "333.1387.fp.risk", "34f1": "", "d402": "", "654a": "", "6e7c": "1920x1080",
		"3c43": innerEnv,
		"54ef": `{"in_new_ab":true,"ab_version":{},"ab_split_num":{}}`,
		"8b94": "", "df35": uuid, "07a4": "zh-CN", "5f45": nil, "db46": 0,
	}
	b, _ := json.Marshal(content)
	return string(b)
}

func sessdataFingerprint(sessdata string) string {
	trimmed := strings.TrimSpace(sessdata)
	if trimmed == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(trimmed))
	return hex.EncodeToString(sum[:])[:12]
}

type LoginStatus struct {
	Configured bool    `json:"configured"`
	IsLogin    bool    `json:"is_login"`
	UName      string  `json:"uname"`
	CheckedAt  float64 `json:"checked_at"`
	Error      string  `json:"error"`
}

func (s LoginStatus) Verified() bool {
	return s.Configured && s.Error == ""
}

func (s LoginStatus) NeedsReconfigure() bool {
	return s.Verified() && !s.IsLogin
}

func (s LoginStatus) Summary() string {
	if !s.Configured {
		return "未配置 sessdata（匿名取数）"
	}
	if s.Error != "" {
		known := "未确认"
		if s.IsLogin {
			known = "有效"
		}
		return fmt.Sprintf("登录态校验失败（%s），沿用上次已知结论=%s", s.Error, known)
	}
	if s.IsLogin {
		uname := s.UName
		if uname == "" {
			uname = "-"
		}
		return fmt.Sprintf("登录态有效（uname=%s）", uname)
	}
	return "登录态已失效（nav 返回 isLogin=false），需重新配置 sessdata"
}

type CredentialManager struct {
	mu                  sync.Mutex
	cacheFile           string
	cookies             map[string]string
	expireAt            map[string]float64
	source              string
	userAgent           string
	lastRefreshTS       float64
	lastSaveTS          float64
	nextRetryTS         float64
	forcedRefreshStreak int
	loginStatus         *LoginStatus
	loginStatusFP       string
	loginLock           sync.Mutex
	cfg                 *Config
	httpClient          *http.Client
	browser             *rendering.Browser
	lastForcedLoginTS   float64
	lastKnownLoginState *bool
	saveMu              sync.Mutex
	saveWg              sync.WaitGroup
}

func NewCredentialManager(path string, cfg *Config, browser *rendering.Browser) *CredentialManager {
	client, err := newHTTPClient(cfg)
	if err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Error("创建凭据 HTTP 客户端失败")
	}
	cm := &CredentialManager{
		cacheFile:  path,
		cookies:    make(map[string]string),
		expireAt:   make(map[string]float64),
		cfg:        cfg,
		httpClient: client,
		browser:    browser,
	}
	cm.load()
	return cm
}

type credentialJSON struct {
	Cookies   map[string]string  `json:"cookies"`
	ExpireAt  map[string]float64 `json:"expire_at"`
	Source    string             `json:"source"`
	UserAgent string             `json:"user_agent"`
	UpdatedAt string             `json:"updated_at"`
}

func (cm *CredentialManager) load() {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	data, err := os.ReadFile(cm.cacheFile)
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			logging.Module("bili_dyn_sub").WithError(err).Error("读取 cookie 缓存失败，将重新生成")
		}
		return
	}
	var raw credentialJSON
	if err := json.Unmarshal(data, &raw); err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Error("解析 cookie 缓存失败，将重新生成")
		return
	}
	cm.cookies = make(map[string]string)
	for k, v := range raw.Cookies {
		if k != "" && v != "" {
			cm.cookies[k] = v
		}
	}
	cm.expireAt = make(map[string]float64)
	for k, v := range raw.ExpireAt {
		cm.expireAt[k] = v
	}
	cm.source = raw.Source
	cm.userAgent = raw.UserAgent
	logging.Module("bili_dyn_sub").Debugf("载入 cookie 缓存: source=%s 字段数=%d", cm.source, len(cm.cookies))
}

func (cm *CredentialManager) Save() error {
	cm.saveMu.Lock()
	defer cm.saveMu.Unlock()

	cm.mu.Lock()
	payload := credentialJSON{
		Cookies:   make(map[string]string, len(cm.cookies)),
		ExpireAt:  make(map[string]float64, len(cm.expireAt)),
		Source:    cm.source,
		UserAgent: cm.userAgent,
		UpdatedAt: time.Now().Format("2006-01-02T15:04:05"),
	}
	for k, v := range cm.cookies {
		payload.Cookies[k] = v
	}
	for k, v := range cm.expireAt {
		payload.ExpireAt[k] = v
	}
	cm.mu.Unlock()

	return utils.WriteJSON(cm.cacheFile, payload)
}

func (cm *CredentialManager) saveLocked() {
	cm.lastSaveTS = float64(time.Now().UnixNano()) / 1e9
	cm.saveWg.Add(1)
	go func() {
		defer cm.saveWg.Done()
		if err := cm.Save(); err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Error("写入 cookie 缓存失败（仅存在内存中）")
		}
	}()
}

func (cm *CredentialManager) Flush() error {
	cm.saveWg.Wait()
	return cm.Save()
}

func (cm *CredentialManager) isCacheValid(now float64) bool {
	if cm.cookies["buvid3"] == "" || len(cm.expireAt) == 0 {
		return false
	}
	if cm.userAgent != "" && cm.userAgent != cm.cfg.UserAgent {
		logging.Module("bili_dyn_sub").Info("配置的 UA 与生成 cookie 时不一致，丢弃缓存重造")
		return false
	}
	minExpire := math.MaxFloat64
	for _, exp := range cm.expireAt {
		if exp < minExpire {
			minExpire = exp
		}
	}
	return now < minExpire
}

func (cm *CredentialManager) compose() map[string]string {
	result := make(map[string]string, len(cm.cookies)+2)
	for k, v := range cm.cookies {
		result[k] = v
	}
	sessdata := strings.TrimSpace(cm.cfg.SessData)
	if sessdata != "" {
		result["SESSDATA"] = sessdata
		jct := strings.TrimSpace(cm.cfg.BiliJCT)
		if jct != "" {
			result["bili_jct"] = jct
		}
	}
	return result
}

func (cm *CredentialManager) BuildHeaders(uid string) http.Header {
	h := make(http.Header)
	h.Set("User-Agent", cm.cfg.UserAgent)
	referer := WWWReferer
	origin := WWWReferer[:len(WWWReferer)-1]
	uid = strings.TrimSpace(uid)
	if uid != "" {
		referer = fmt.Sprintf(SpaceURLTemplate, uid)
		origin = SpaceOrigin
	}
	h.Set("Referer", referer)
	h.Set("Origin", origin)
	h.Set("Accept", "application/json, text/plain, */*")
	h.Set("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
	return h
}

func (cm *CredentialManager) MarkFetchSuccess() {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	cm.forcedRefreshStreak = 0
}

func (cm *CredentialManager) UpdateFromResponse(cookies []*http.Cookie) {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	now := float64(time.Now().UnixNano()) / 1e9
	changed := false
	for _, c := range cookies {
		name := c.Name
		val := c.Value
		if val == "" || !mutableCookieKeys[name] || cm.cookies[name] == val {
			continue
		}
		cm.cookies[name] = val
		changed = true
		if name == "bili_ticket" {
			cm.expireAt["bili_ticket"] = now + TicketTTLSeconds
		}
		if name == "buvid3" {
			cm.expireAt["buvid3"] = now + BuvidTTLSeconds
		}
	}

	if !changed {
		return
	}
	if now-cm.lastSaveTS >= SaveThrottleSeconds {
		cm.saveLocked()
	}
}

func (cm *CredentialManager) GetCookies(ctx context.Context, forceRefresh bool) (map[string]string, error) {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	now := float64(time.Now().UnixNano()) / 1e9
	if forceRefresh {
		if now-cm.lastRefreshTS > ForcedStreakReset {
			cm.forcedRefreshStreak = 0
		}
		cm.forcedRefreshStreak++
		if len(cm.cookies) > 0 && now-cm.lastRefreshTS < MinRefreshInterval {
			logging.Module("bili_dyn_sub").Debugf("距上次刷新不足 %ds，沿用现有 cookie", int(MinRefreshInterval))
			return cm.compose(), nil
		}
	} else if cm.isCacheValid(now) {
		return cm.compose(), nil
	} else if now < cm.nextRetryTS {
		logging.Module("bili_dyn_sub").Debug("上次 cookie 生成失败仍处于冷却窗口，沿用现有 cookie")
		return cm.compose(), nil
	}

	escalate := forceRefresh && cm.forcedRefreshStreak > 1
	cm.refreshLocked(ctx, escalate)
	return cm.compose(), nil
}

func (cm *CredentialManager) refreshLocked(ctx context.Context, escalate bool) bool {
	cm.lastRefreshTS = float64(time.Now().UnixNano()) / 1e9
	type stepFunc func(context.Context) (map[string]string, bool, string, error)
	var steps []stepFunc
	if escalate {
		steps = []stepFunc{cm.acquireByBrowser, cm.acquireByHTTP}
	} else {
		steps = []stepFunc{cm.acquireByHTTP, cm.acquireByBrowser}
	}

	for _, step := range steps {
		cookies, degraded, source, err := step(ctx)
		if err != nil || cookies == nil || cookies["buvid3"] == "" {
			continue
		}
		now := float64(time.Now().UnixNano()) / 1e9
		cm.cookies = make(map[string]string, len(cookies))
		for k, v := range cookies {
			if k != "" && v != "" {
				cm.cookies[k] = v
			}
		}
		exp := map[string]float64{
			"buvid3":      now + BuvidTTLSeconds,
			"bili_ticket": now + TicketTTLSeconds,
		}
		if degraded {
			for k, v := range exp {
				exp[k] = math.Min(v, now+DegradedTTLSeconds)
			}
		}
		cm.expireAt = exp
		cm.source = source
		cm.userAgent = cm.cfg.UserAgent
		cm.saveLocked()
		cm.nextRetryTS = 0.0

		tag := ""
		if degraded {
			tag = fmt.Sprintf("（指纹激活或 ticket 缺失，%ds 后重试）", int(DegradedTTLSeconds))
		}
		logging.Module("bili_dyn_sub").Infof("已生成 B 站 cookie（%s）: 字段数=%d%s", source, len(cm.cookies), tag)
		return true
	}

	now := float64(time.Now().UnixNano()) / 1e9
	cm.nextRetryTS = now + FailedRetrySeconds
	if len(cm.cookies) > 0 {
		logging.Module("bili_dyn_sub").Warnf("各级 cookie 生成均失败，沿用现有缓存（可能已过期），%ds 后再试", int(FailedRetrySeconds))
	} else {
		logging.Module("bili_dyn_sub").Errorf("各级 cookie 生成均失败且无缓存，本轮取数大概率 -352，%ds 后再试", int(FailedRetrySeconds))
	}
	return false
}

func (cm *CredentialManager) acquireByHTTP(ctx context.Context) (map[string]string, bool, string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, SPIURL, nil)
	if err != nil {
		return nil, false, "", err
	}
	req.Header = cm.BuildHeaders("")
	resp, err := cm.httpClient.Do(req)
	if err != nil {
		return nil, false, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, false, "", fmt.Errorf("spi HTTP %d", resp.StatusCode)
	}

	var buf bytes.Buffer
	buf.ReadFrom(resp.Body)
	raw := gjson.ParseBytes(buf.Bytes())
	if raw.Get("code").Int() != 0 {
		return nil, false, "", fmt.Errorf("spi code %d", raw.Get("code").Int())
	}
	buvid3 := strings.TrimSpace(raw.Get("data.b_3").String())
	buvid4 := strings.TrimSpace(raw.Get("data.b_4").String())
	if buvid3 == "" {
		return nil, false, "", errors.New("missing buvid3 in spi")
	}

	uuid := genUUIDInfoc()
	payload := buildActivatePayload(uuid, cm.cfg.UserAgent)
	cookies := map[string]string{
		"buvid3":   buvid3,
		"buvid4":   buvid4,
		"b_nut":    strconv.FormatInt(time.Now().Unix(), 10),
		"_uuid":    uuid,
		"buvid_fp": genBuvidFp(payload),
		"b_lsid":   genBLSID(),
	}

	activated := cm.activateHTTP(ctx, payload, cookies)
	ticket, ticketExp := cm.fetchTicketHTTP(ctx)
	if ticket != "" {
		cookies["bili_ticket"] = ticket
		cookies["bili_ticket_expires"] = strconv.FormatInt(ticketExp, 10)
	}
	degraded := !activated || ticket == ""
	return cookies, degraded, "http", nil
}

func (cm *CredentialManager) activateHTTP(ctx context.Context, payload string, cookies map[string]string) bool {
	bodyMap := map[string]string{"payload": payload}
	b, _ := json.Marshal(bodyMap)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ExClimbURL, bytes.NewReader(b))
	if err != nil {
		return false
	}
	req.Header = cm.BuildHeaders("")
	req.Header.Set("Content-Type", "application/json")
	var cookieParts []string
	for k, v := range cookies {
		cookieParts = append(cookieParts, k+"="+v)
	}
	req.Header.Set("Cookie", strings.Join(cookieParts, "; "))

	resp, err := cm.httpClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false
	}
	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return false
	}
	if !gjson.ValidBytes(bodyBytes) {
		return false
	}
	parsed := gjson.ParseBytes(bodyBytes)
	if !parsed.IsObject() {
		return false
	}
	codeRes := parsed.Get("code")
	if !codeRes.Exists() || codeRes.Type != gjson.Number || codeRes.Int() != 0 {
		return false
	}
	dataCode := parsed.Get("data.code")
	if dataCode.Exists() && dataCode.Int() != 0 {
		return false
	}
	return true
}

func (cm *CredentialManager) fetchTicketHTTP(ctx context.Context) (string, int64) {
	ts := time.Now().Unix()
	q := bilibili.BuildTicketParams(ts)
	ticketURL := TicketURL + "?" + q.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ticketURL, nil)
	if err != nil {
		return "", 0
	}
	req.Header = cm.BuildHeaders("")
	resp, err := cm.httpClient.Do(req)
	if err != nil {
		return "", 0
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", 0
	}
	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", 0
	}
	if !gjson.ValidBytes(bodyBytes) {
		return "", 0
	}
	parsed := gjson.ParseBytes(bodyBytes)
	if !parsed.IsObject() {
		return "", 0
	}
	codeRes := parsed.Get("code")
	if !codeRes.Exists() || codeRes.Type != gjson.Number || codeRes.Int() != 0 {
		return "", 0
	}
	ticket := strings.TrimSpace(parsed.Get("data.ticket").String())
	if ticket == "" {
		return "", 0
	}
	createdAt := parsed.Get("data.created_at").Int()
	if createdAt <= 0 {
		createdAt = ts
	}
	ttl := parsed.Get("data.ttl").Int()
	if ttl <= 0 {
		ttl = int64(TicketTTLSeconds)
	}
	return ticket, createdAt + ttl
}

func (cm *CredentialManager) acquireByBrowser(ctx context.Context) (map[string]string, bool, string, error) {
	if !cm.cfg.EnablePlaywrightFallback || cm.browser == nil {
		return nil, false, "", errors.New("browser fallback disabled")
	}

	randUID := 1 + (time.Now().UnixNano() % 1000)
	targetURL := fmt.Sprintf(SpaceURLTemplate, strconv.FormatInt(randUID, 10))
	logging.Module("bili_dyn_sub").Infof("升级到 L2 浏览器兜底造 cookie: %s", targetURL)

	cookies := make(map[string]string)
	opts := rendering.BrowserContextOptions{
		Proxy:     cm.cfg.EffectiveProxy(),
		UserAgent: cm.cfg.UserAgent,
		Locale:    "zh-CN",
	}

	err := cm.browser.RunContext(ctx, opts, func(tabCtx context.Context) error {
		navTimeout, navCancel := context.WithTimeout(tabCtx, 30*time.Second)
		defer navCancel()

		// Track network idle
		activeRequests := make(map[cdpnetwork.RequestID]bool)
		var reqMu sync.Mutex
		idleCh := make(chan struct{}, 1)
		var idleTimer *time.Timer

		chromedp.ListenTarget(tabCtx, func(ev any) {
			reqMu.Lock()
			defer reqMu.Unlock()
			switch e := ev.(type) {
			case *cdpnetwork.EventRequestWillBeSent:
				activeRequests[e.RequestID] = true
				if idleTimer != nil {
					idleTimer.Stop()
					idleTimer = nil
				}
				// Drain stale idle signal
				select {
				case <-idleCh:
				default:
				}
			case *cdpnetwork.EventLoadingFinished:
				delete(activeRequests, e.RequestID)
				if len(activeRequests) == 0 {
					if idleTimer != nil {
						idleTimer.Stop()
					}
					idleTimer = time.AfterFunc(500*time.Millisecond, func() {
						reqMu.Lock()
						defer reqMu.Unlock()
						if len(activeRequests) == 0 {
							select {
							case idleCh <- struct{}{}:
							default:
							}
						}
					})
				}
			case *cdpnetwork.EventLoadingFailed:
				delete(activeRequests, e.RequestID)
				if len(activeRequests) == 0 {
					if idleTimer != nil {
						idleTimer.Stop()
					}
					idleTimer = time.AfterFunc(500*time.Millisecond, func() {
						reqMu.Lock()
						defer reqMu.Unlock()
						if len(activeRequests) == 0 {
							select {
							case idleCh <- struct{}{}:
							default:
							}
						}
					})
				}
			}
		})

		if err := chromedp.Run(navTimeout, chromedp.Navigate(targetURL)); err != nil {
			return err
		}

		// Wait for document.cookie contains bili_ticket (up to 20s)
		ticketTimeout, ticketCancel := context.WithTimeout(tabCtx, 20*time.Second)
		defer ticketCancel()
		var hasTicket bool
		if err := chromedp.Run(ticketTimeout, chromedp.Poll(`document.cookie.includes("bili_ticket")`, &hasTicket, chromedp.WithPollingInterval(200*time.Millisecond), chromedp.WithPollingTimeout(20*time.Second))); err != nil {
			return fmt.Errorf("wait bili_ticket poll: %w", err)
		}

		// Wait for network idle (up to 20s)
		select {
		case <-idleCh:
		case <-time.After(20 * time.Second):
			return errors.New("等待浏览器网络空闲超时 (20s)")
		case <-tabCtx.Done():
			return tabCtx.Err()
		}

		// Read cookies
		var rawCookies []*cdpnetwork.Cookie
		if err := chromedp.Run(tabCtx, chromedp.ActionFunc(func(ctx context.Context) error {
			var err error
			rawCookies, err = storage.GetCookies().Do(ctx)
			return err
		})); err != nil {
			return err
		}
		for _, c := range rawCookies {
			if c.Name != "" && c.Value != "" {
				cookies[c.Name] = c.Value
			}
		}
		return nil
	})

	if err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Warn("L2 浏览器造 cookie 失败")
		return nil, false, "", err
	}
	if cookies["buvid3"] == "" {
		return nil, false, "", errors.New("browser cookies missing buvid3")
	}

	if cookies["_uuid"] == "" {
		cookies["_uuid"] = genUUIDInfoc()
	}
	if cookies["b_lsid"] == "" {
		cookies["b_lsid"] = genBLSID()
	}

	payload := buildActivatePayload(cookies["_uuid"], cm.cfg.UserAgent)
	activated := cm.activateHTTP(ctx, payload, cookies)
	degraded := !activated || cookies["bili_ticket"] == ""
	return cookies, degraded, "browser", nil
}

func (cm *CredentialManager) VerifyLogin(ctx context.Context, force bool) (LoginStatus, error) {
	cm.loginLock.Lock()
	defer cm.loginLock.Unlock()

	sessdata := strings.TrimSpace(cm.cfg.SessData)
	if sessdata == "" {
		status := LoginStatus{Configured: false, CheckedAt: float64(time.Now().UnixNano()) / 1e9}
		cm.loginStatus = &status
		cm.loginStatusFP = ""
		return status, nil
	}

	fp := sessdataFingerprint(sessdata)
	recordStatus := func(st LoginStatus) (LoginStatus, error) {
		cm.loginStatus = &st
		cm.loginStatusFP = fp
		return st, nil
	}

	now := float64(time.Now().UnixNano()) / 1e9
	if !force && cm.loginStatus != nil && cm.loginStatus.Configured && cm.loginStatusFP == fp {
		ttl := LoginStatusTTL
		if cm.loginStatus.Error != "" {
			ttl = LoginErrorRetryTTL
		}
		if now-cm.loginStatus.CheckedAt < ttl {
			return *cm.loginStatus, nil
		}
	}

	fallbackLogin := false
	fallbackUName := ""
	if cm.loginStatus != nil && cm.loginStatusFP == fp {
		fallbackLogin = cm.loginStatus.IsLogin
		fallbackUName = cm.loginStatus.UName
	}

	cookies, err := cm.GetCookies(ctx, false)
	if err != nil {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      err.Error(),
		}
		return recordStatus(status)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, NavURL, nil)
	if err != nil {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      err.Error(),
		}
		return recordStatus(status)
	}
	req.Header = cm.BuildHeaders("")
	var parts []string
	for k, v := range cookies {
		parts = append(parts, k+"="+v)
	}
	req.Header.Set("Cookie", strings.Join(parts, "; "))

	resp, err := cm.httpClient.Do(req)
	if err != nil {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      err.Error(),
		}
		return recordStatus(status)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      fmt.Sprintf("HTTP %d", resp.StatusCode),
		}
		return recordStatus(status)
	}

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      fmt.Sprintf("读取响应失败: %v", err),
		}
		return recordStatus(status)
	}
	if !gjson.ValidBytes(bodyBytes) {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      "nav 响应不是有效 JSON",
		}
		return recordStatus(status)
	}
	parsed := gjson.ParseBytes(bodyBytes)
	if !parsed.IsObject() {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      "nav 响应顶层不是对象",
		}
		return recordStatus(status)
	}
	codeRes := parsed.Get("code")
	if !codeRes.Exists() || codeRes.Type != gjson.Number {
		status := LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      "nav 响应缺少 code 或类型不匹配",
		}
		return recordStatus(status)
	}
	code := codeRes.Int()

	var status LoginStatus
	if code == 0 {
		isLogin := parsed.Get("data.isLogin").Bool()
		uname := ""
		if isLogin {
			uname = strings.TrimSpace(parsed.Get("data.uname").String())
		}
		status = LoginStatus{
			Configured: true,
			IsLogin:    isLogin,
			UName:      uname,
			CheckedAt:  now,
		}
	} else if code == -101 {
		status = LoginStatus{
			Configured: true,
			IsLogin:    false,
			UName:      "",
			CheckedAt:  now,
		}
	} else {
		status = LoginStatus{
			Configured: true,
			IsLogin:    fallbackLogin,
			UName:      fallbackUName,
			CheckedAt:  now,
			Error:      fmt.Sprintf("nav code=%d msg=%s", code, parsed.Get("message").String()),
		}
	}

	return recordStatus(status)
}

func (cm *CredentialManager) GetLoginStatus() *LoginStatus {
	cm.loginLock.Lock()
	defer cm.loginLock.Unlock()
	if cm.loginStatus == nil {
		return nil
	}
	cp := *cm.loginStatus
	return &cp
}

func (cm *CredentialManager) Describe() string {
	loginStatus := cm.GetLoginStatus()

	cm.mu.Lock()
	defer cm.mu.Unlock()

	mode := "未生成"
	if strings.TrimSpace(cm.cfg.SessData) != "" {
		loginDesc := "(未校验)"
		if loginStatus != nil && loginStatus.Configured {
			if loginStatus.Error != "" {
				loginDesc = fmt.Sprintf("(校验失败: %s)", loginStatus.Error)
			} else if loginStatus.IsLogin {
				uname := loginStatus.UName
				if uname == "" {
					uname = "-"
				}
				loginDesc = fmt.Sprintf("(uname=%s)", uname)
			} else {
				loginDesc = "(已失效)"
			}
		}
		mode = fmt.Sprintf("L0 登录态%s", loginDesc)
	} else if cm.source == "http" {
		mode = "L1 纯 HTTP"
	} else if cm.source == "browser" {
		mode = "L2 Playwright"
	}

	earliest := "-"
	if len(cm.expireAt) > 0 {
		minExp := math.MaxFloat64
		for _, exp := range cm.expireAt {
			if exp < minExp {
				minExp = exp
			}
		}
		t := time.Unix(int64(minExp), 0)
		earliest = t.Format("2006-01-02T15:04")
	}

	return fmt.Sprintf("cookie 来源=%s 缓存字段数=%d 最早到期=%s", mode, len(cm.cookies), earliest)
}
