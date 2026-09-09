package bili_dyn_sub

import (
	"encoding/json"
	"errors"
	"os"
	"strings"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

const (
	DefaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

var forbiddenUATokens = []string{"python", "httpx", "curl", "aiohttp", "requests"}

type Config struct {
	PollIntervalSeconds      int     `json:"poll_interval_seconds"`
	PollJitterSeconds        int     `json:"poll_jitter_seconds"`
	UIDRequestGapSeconds     float64 `json:"uid_request_gap_seconds"`
	MaxDynamicAgeMinutes     int     `json:"max_dynamic_age_minutes"`
	MaxPushPerRound          int     `json:"max_push_per_round"`
	SessData                 string  `json:"sessdata"`
	BiliJCT                  string  `json:"bili_jct"`
	Proxy                    *string `json:"proxy"`
	EnableWBI                bool    `json:"enable_wbi"`
	EnablePlaywrightFallback bool    `json:"enable_playwright_fallback"`
	HTTPTimeoutConnect       float64 `json:"http_timeout_connect"`
	HTTPTimeoutTotal         float64 `json:"http_timeout_total"`
	UserAgent                string  `json:"user_agent"`
	SeenIDsMax               int     `json:"seen_ids_max"`
	SeenRetentionDays        int     `json:"seen_retention_days"`
	SendIntervalSeconds      float64 `json:"send_interval_seconds"`
	SendRetryTimes           int     `json:"send_retry_times"`
	TextTruncateLength       int     `json:"text_truncate_length"`
}

func DefaultConfig() Config {
	enablePlaywright := true
	return Config{
		PollIntervalSeconds:      100,
		PollJitterSeconds:        20,
		UIDRequestGapSeconds:     8.0,
		MaxDynamicAgeMinutes:     30,
		MaxPushPerRound:          5,
		SessData:                 "",
		BiliJCT:                  "",
		Proxy:                    nil,
		EnableWBI:                false,
		EnablePlaywrightFallback: enablePlaywright,
		HTTPTimeoutConnect:       5.0,
		HTTPTimeoutTotal:         10.0,
		UserAgent:                DefaultUserAgent,
		SeenIDsMax:               50,
		SeenRetentionDays:        14,
		SendIntervalSeconds:      1.5,
		SendRetryTimes:           3,
		TextTruncateLength:       500,
	}
}

func validateUserAgent(ua string) string {
	trimmed := strings.TrimSpace(ua)
	if trimmed == "" {
		return DefaultUserAgent
	}
	lower := strings.ToLower(trimmed)
	for _, token := range forbiddenUATokens {
		if strings.Contains(lower, token) {
			logging.Module("bili_dyn_sub").Warnf("配置的 user_agent 包含 %q，已回落到默认 UA", token)
			return DefaultUserAgent
		}
	}
	return trimmed
}

func (c *Config) sanitize() {
	if c.PollIntervalSeconds < 30 {
		c.PollIntervalSeconds = 30
	}
	if c.PollJitterSeconds < 0 {
		c.PollJitterSeconds = 0
	}
	if c.UIDRequestGapSeconds < 0 {
		c.UIDRequestGapSeconds = 0
	}
	if c.MaxDynamicAgeMinutes < 0 {
		c.MaxDynamicAgeMinutes = 0
	}
	if c.MaxPushPerRound < 0 {
		c.MaxPushPerRound = 0
	}
	if c.HTTPTimeoutConnect < 1.0 {
		c.HTTPTimeoutConnect = 1.0
	}
	if c.HTTPTimeoutTotal < 1.0 {
		c.HTTPTimeoutTotal = 1.0
	}
	if c.SeenIDsMax < 1 {
		c.SeenIDsMax = 1
	}
	if c.SeenRetentionDays < 1 {
		c.SeenRetentionDays = 1
	}
	if c.SendIntervalSeconds < 0 {
		c.SendIntervalSeconds = 0
	}
	if c.SendRetryTimes < 1 {
		c.SendRetryTimes = 1
	}
	if c.TextTruncateLength <= 0 {
		c.TextTruncateLength = 500
	}
	c.UserAgent = validateUserAgent(c.UserAgent)
}

func (c *Config) EffectiveProxy() string {
	if c.Proxy != nil && strings.TrimSpace(*c.Proxy) != "" {
		return strings.TrimSpace(*c.Proxy)
	}
	for _, env := range []string{"HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"} {
		if val := strings.TrimSpace(os.Getenv(env)); val != "" {
			return val
		}
	}
	return ""
}

func (c *Config) HTTPProxy() string {
	eff := c.EffectiveProxy()
	if eff == "" {
		return "direct"
	}
	return eff
}

func LoadConfig(path string) Config {
	cfg := DefaultConfig()
	data, err := os.ReadFile(path)
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			logging.Module("bili_dyn_sub").WithError(err).Warn("读取 config.json 失败，使用默认配置")
		}
		return cfg
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Warn("解析 config.json 失败，使用默认配置")
		return cfg
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		logging.Module("bili_dyn_sub").WithError(err).Warn("校验 config.json 失败，使用默认配置")
		return DefaultConfig()
	}
	cfg.sanitize()
	return cfg
}
