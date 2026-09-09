package analysis_bilibili

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	zero "github.com/wdvxdr1123/ZeroBot"
)

type target struct {
	Kind         string // video / bangumi_ep / bangumi_ss / bangumi_md / live / article / dynamic
	ID           string // BVID 保留原大小写，其他 ID 用十进制字符串
	AID          bool   // video 的 ID 是否为 aid
	Page         int    // 0 表示未指定
	Time         *int   // nil 表示未指定，t=0 仍需保留
	DynamicByRID bool
}

type extractResult struct {
	Target      *target
	SearchTitle string
}

var (
	httpURLRegex        = regexp.MustCompile(`(?i)https?://[^\s\x{4e00}-\x{9fa5}"]+`)
	schemelessBiliRegex = regexp.MustCompile(`(?i)(?:^|[^\w/.-])((?:(?:[a-zA-Z0-9-]+\.)*bilibili\.com|b23\.tv|bili(?:22|23|33|2233)\.cn)/[^\s\x{4e00}-\x{9fa5}"]*)`)

	bareBVRegex = regexp.MustCompile(`^BV[0-9a-zA-Z]{10}\b`)
	bareAVRegex = regexp.MustCompile(`^av\d+\b`)
	bareCVRegex = regexp.MustCompile(`^cv\d+\b`)

	bvRegex       = regexp.MustCompile(`\b(BV[0-9a-zA-Z]{10})\b`)
	aidRegex      = regexp.MustCompile(`(?:/video/av|aid=|\bav)(\d+)`)
	epidRegex     = regexp.MustCompile(`(?:/bangumi/play/ep|ep_id=|\bep)(\d+)`)
	ssidRegex     = regexp.MustCompile(`(?:/bangumi/play/ss|season_id=|\bss)(\d+)`)
	mdidRegex     = regexp.MustCompile(`(?:/bangumi/media/md|media_id=|\bmd)(\d+)`)
	liveRegex     = regexp.MustCompile(`(?i)(?:live\.bilibili\.com/(?:blanc/|h5/)?|room_id=)(\d+)`)
	cvRegex       = regexp.MustCompile(`(?:/read/(?:cv|mobile|native)(?:/|\?id=)?|^cv)(\d+)`)
	opusRegex     = regexp.MustCompile(`(?i)bilibili\.com/opus/(\d+)`)
	dynamicTRegex = regexp.MustCompile(`(?i)(?:t|m)\.bilibili\.com/(?:opus/)?(\d+)`)
)

var shareURLKeys = []string{"qqdocurl", "jumpUrl", "jump_url", "qqdocUrl", "url"}

func isValidBiliHost(host string) bool {
	host = strings.ToLower(host)
	if colon := strings.Index(host, ":"); colon != -1 {
		host = host[:colon]
	}
	if host == "bilibili.com" || strings.HasSuffix(host, ".bilibili.com") {
		return true
	}
	switch host {
	case "b23.tv", "bili22.cn", "bili23.cn", "bili33.cn", "bili2233.cn":
		return true
	}
	return false
}

func parseBiliURL(raw string) (*url.URL, bool) {
	rawTrimmed := strings.TrimSpace(raw)
	lowerRaw := strings.ToLower(rawTrimmed)
	if !strings.HasPrefix(lowerRaw, "http://") && !strings.HasPrefix(lowerRaw, "https://") {
		rawTrimmed = "https://" + rawTrimmed
	}
	u, err := url.Parse(rawTrimmed)
	if err != nil {
		return nil, false
	}
	u.Scheme = strings.ToLower(u.Scheme)
	if u.Scheme != "http" && u.Scheme != "https" {
		return nil, false
	}
	u.Host = strings.ToLower(u.Host)
	if !isValidBiliHost(u.Hostname()) {
		return nil, false
	}
	return u, true
}

func isShortlink(u *url.URL) bool {
	switch strings.ToLower(u.Hostname()) {
	case "b23.tv", "bili22.cn", "bili23.cn", "bili33.cn", "bili2233.cn":
		return len(strings.Trim(u.Path, "/")) > 0
	}
	return false
}

func trimTrailingPunctuation(s string) string {
	return strings.TrimRight(s, `.,)>)]"';:!?，。！？’”；：）》】`)
}

func extractBiliURLs(text string) []*url.URL {
	var results []*url.URL
	seen := make(map[string]bool)

	for _, raw := range httpURLRegex.FindAllString(text, -1) {
		clean := trimTrailingPunctuation(raw)
		u, ok := parseBiliURL(clean)
		if !ok {
			continue
		}
		full := u.String()
		if !seen[full] {
			seen[full] = true
			results = append(results, u)
		}
	}

	for _, m := range schemelessBiliRegex.FindAllStringSubmatch(text, -1) {
		if len(m) < 2 {
			continue
		}
		candidate := trimTrailingPunctuation(m[1])
		u, ok := parseBiliURL(candidate)
		if !ok {
			continue
		}
		full := u.String()
		if !seen[full] {
			seen[full] = true
			results = append(results, u)
		}
	}

	return results
}

func findShareURLInJSON(data any) string {
	switch v := data.(type) {
	case map[string]any:
		for _, k := range shareURLKeys {
			if val, ok := v[k].(string); ok && val != "" {
				if _, ok := parseBiliURL(val); ok {
					return val
				}
			}
		}
		for _, val := range v {
			if found := findShareURLInJSON(val); found != "" {
				return found
			}
		}
	case []any:
		for _, item := range v {
			if found := findShareURLInJSON(item); found != "" {
				return found
			}
		}
	}
	return ""
}

func findCardSearchTitle(data any) string {
	switch v := data.(type) {
	case map[string]any:
		desc, _ := v["desc"].(string)
		desc = strings.TrimSpace(desc)
		if desc != "" && !strings.Contains(desc, "哔哩哔哩") {
			return desc
		}
		for _, val := range v {
			if found := findCardSearchTitle(val); found != "" {
				return found
			}
		}
	case []any:
		for _, item := range v {
			if found := findCardSearchTitle(item); found != "" {
				return found
			}
		}
	}
	return ""
}

func parseJSONData(raw string) (any, bool) {
	var obj any
	if err := json.Unmarshal([]byte(raw), &obj); err == nil {
		return obj, true
	}
	unescaped := html.UnescapeString(raw)
	unescaped = strings.ReplaceAll(unescaped, `\/`, "/")
	unescaped = strings.ReplaceAll(unescaped, "&#44;", ",")
	unescaped = strings.ReplaceAll(unescaped, "&#91;", "[")
	unescaped = strings.ReplaceAll(unescaped, "&#93;", "]")
	if err := json.Unmarshal([]byte(unescaped), &obj); err == nil {
		return obj, true
	}
	start := strings.Index(unescaped, "{")
	end := strings.LastIndex(unescaped, "}")
	if start >= 0 && end > start {
		if err := json.Unmarshal([]byte(unescaped[start:end+1]), &obj); err == nil {
			return obj, true
		}
	}
	return nil, false
}

func expandShortURL(ctx context.Context, client *http.Client, shortURL string) (string, error) {
	current := shortURL
	lowerCurrent := strings.ToLower(current)
	if !strings.HasPrefix(lowerCurrent, "http://") && !strings.HasPrefix(lowerCurrent, "https://") {
		current = "https://" + current
	}

	redirectClient := &http.Client{
		Transport: client.Transport,
		Timeout:   20 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return errors.New("短链重定向超过 5 次")
			}
			if !isValidBiliHost(req.URL.Hostname()) {
				return fmt.Errorf("非法重定向域名: %s", req.URL.Hostname())
			}
			return nil
		},
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, current, nil)
	if err != nil {
		return shortURL, err
	}
	req.Header.Set("User-Agent", defaultUserAgent)
	req.Header.Set("Referer", biliReferer)

	resp, err := redirectClient.Do(req)
	if err != nil {
		return shortURL, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusPreconditionFailed {
		return shortURL, ErrHTTP412
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 400 {
		return shortURL, fmt.Errorf("短链 HTTP %d", resp.StatusCode)
	}

	if resp.Request != nil && resp.Request.URL != nil {
		return resp.Request.URL.String(), nil
	}
	return shortURL, nil
}

func parseURLQuery(text string) (int, *int) {
	rawClean := strings.ReplaceAll(text, "&amp;", "&")
	var u *url.URL
	lowerClean := strings.ToLower(rawClean)
	if strings.HasPrefix(lowerClean, "http://") || strings.HasPrefix(lowerClean, "https://") {
		u, _ = url.Parse(rawClean)
	} else if loc := httpURLRegex.FindString(rawClean); loc != "" {
		u, _ = url.Parse(trimTrailingPunctuation(loc))
	} else if strings.Contains(rawClean, "?") {
		u, _ = url.Parse("https://" + strings.TrimPrefix(rawClean, "//"))
	}

	var page int
	var timeLoc *int
	if u != nil {
		q := u.Query()
		if pStr := q.Get("p"); pStr != "" {
			if pVal, err := strconv.Atoi(pStr); err == nil && pVal > 0 {
				page = pVal
			}
		}
		if tStr := q.Get("t"); tStr != "" {
			if tVal, err := strconv.Atoi(tStr); err == nil && tVal >= 0 {
				timeLoc = &tVal
			}
		}
	}
	return page, timeLoc
}

func extractTargetFromText(text string) *target {
	page, timeLoc := parseURLQuery(text)

	// Priority 1: BV
	if bm := bvRegex.FindStringSubmatch(text); len(bm) > 1 {
		return &target{
			Kind: "video",
			ID:   bm[1],
			AID:  false,
			Page: page,
			Time: timeLoc,
		}
	}

	// Priority 2: av
	if am := aidRegex.FindStringSubmatch(text); len(am) > 1 {
		return &target{
			Kind: "video",
			ID:   am[1],
			AID:  true,
			Page: page,
			Time: timeLoc,
		}
	}

	// Priority 3: bangumi ep
	if em := epidRegex.FindStringSubmatch(text); len(em) > 1 {
		return &target{
			Kind: "bangumi_ep",
			ID:   em[1],
			Time: timeLoc,
		}
	}

	// Priority 4: bangumi ss
	if sm := ssidRegex.FindStringSubmatch(text); len(sm) > 1 {
		return &target{
			Kind: "bangumi_ss",
			ID:   sm[1],
			Time: timeLoc,
		}
	}

	// Priority 5: bangumi md
	if mm := mdidRegex.FindStringSubmatch(text); len(mm) > 1 {
		return &target{
			Kind: "bangumi_md",
			ID:   mm[1],
			Time: timeLoc,
		}
	}

	// Priority 6: live
	if lm := liveRegex.FindStringSubmatch(text); len(lm) > 1 {
		return &target{
			Kind: "live",
			ID:   lm[1],
		}
	}

	// Priority 7: article (cv)
	if cm := cvRegex.FindStringSubmatch(text); len(cm) > 1 {
		return &target{
			Kind: "article",
			ID:   cm[1],
		}
	}

	// Priority 8: dynamic opus
	if om := opusRegex.FindStringSubmatch(text); len(om) > 1 {
		return &target{
			Kind: "dynamic",
			ID:   om[1],
		}
	}

	// Priority 9: dynamic type=2
	if dm := dynamicTRegex.FindStringSubmatch(text); len(dm) > 1 {
		isType2 := false
		rawClean := strings.ReplaceAll(text, "&amp;", "&")
		if u, err := url.Parse(rawClean); err == nil {
			if u.Query().Get("type") == "2" {
				isType2 = true
			}
		} else if strings.Contains(rawClean, "type=2") {
			isType2 = true
		}
		if isType2 {
			return &target{
				Kind:         "dynamic",
				ID:           dm[1],
				DynamicByRID: true,
			}
		}
		// Priority 10: normal dynamic
		return &target{
			Kind: "dynamic",
			ID:   dm[1],
		}
	}

	return nil
}

func extractTarget(ctx context.Context, client *http.Client, botCtx *zero.Ctx) (*target, string, error) {
	type expandEntry struct {
		url string
		err error
	}
	expandedCache := make(map[string]expandEntry)
	var lastExpandErr error

	expandOnce := func(shortURL string) (string, error) {
		if entry, ok := expandedCache[shortURL]; ok {
			return entry.url, entry.err
		}
		expanded, err := expandShortURL(ctx, client, shortURL)
		if err != nil {
			logging.Event(pluginID, botCtx.Event).WithError(err).Warnf("展开短链 %s 失败", shortURL)
			expandedCache[shortURL] = expandEntry{url: shortURL, err: err}
			return shortURL, err
		}
		if expanded == "" {
			expanded = shortURL
		}
		expandedCache[shortURL] = expandEntry{url: expanded, err: nil}
		return expanded, nil
	}

	// 1. Traverse JSON segments
	var searchTitle string
	for _, seg := range botCtx.Event.Message {
		if seg.Type != "json" {
			continue
		}
		raw := seg.Data["data"]
		if raw == "" {
			continue
		}
		obj, ok := parseJSONData(raw)
		if !ok {
			continue
		}
		shareURL := findShareURLInJSON(obj)
		if shareURL != "" {
			targetURL := shareURL
			if u, ok := parseBiliURL(shareURL); ok {
				if isShortlink(u) {
					expanded, err := expandOnce(u.String())
					if err != nil {
						lastExpandErr = err
					}
					targetURL = expanded
				} else {
					targetURL = u.String()
				}
			}
			if tgt := extractTargetFromText(targetURL); tgt != nil {
				return tgt, "", nil
			}
		}
		if searchTitle == "" {
			rawStr := fmt.Sprint(raw)
			if strings.Contains(rawStr, "哔哩哔哩") {
				searchTitle = findCardSearchTitle(obj)
			}
		}
	}

	// 2. 检查普通文本消息（只遍历文本段，避免包含 JSON 卡片或 CQ 码）
	var textParts []string
	for _, seg := range botCtx.Event.Message {
		if seg.Type == "text" {
			textParts = append(textParts, seg.Data["text"])
		}
	}
	textMsg := strings.Join(textParts, " ")
	trimmed := strings.TrimSpace(textMsg)

	// 开头裸 av/cv/BV 号
	if bareBVRegex.MatchString(trimmed) || bareAVRegex.MatchString(trimmed) || bareCVRegex.MatchString(trimmed) {
		if tgt := extractTargetFromText(trimmed); tgt != nil {
			return tgt, "", nil
		}
	}

	// 提取消息中完整的合法 B 站链接与短链（排除类似 notbilibili.com 的误匹配）
	biliURLs := extractBiliURLs(textMsg)
	for _, u := range biliURLs {
		targetURL := u.String()
		if isShortlink(u) {
			expanded, err := expandOnce(u.String())
			if err != nil {
				lastExpandErr = err
			}
			targetURL = expanded
		}
		if tgt := extractTargetFromText(targetURL); tgt != nil {
			return tgt, "", nil
		}
	}

	if searchTitle != "" {
		return nil, searchTitle, nil
	}

	if lastExpandErr != nil {
		return nil, "", lastExpandErr
	}

	return nil, "", nil
}
