package bili_dyn_sub

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/bilibili"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/tidwall/gjson"
)

const (
	FeedSpaceURL    = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
	LiveUserInfoURL = "https://api.live.bilibili.com/live_user/v1/Master/info"
	TimezoneOffset  = "-480"
	Platform        = "web"
	Features        = "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote"
	WebLocation     = "333.1387"
	DmImgList       = "[]"
	DmImgInter      = `{"ds":[],"wh":[0,0,0],"of":[0,0,0]}`
	FakeRenderer    = "Google Inc. (Intel)~ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"
)

var dmCoverImgStr = func() string {
	b64 := base64.StdEncoding.EncodeToString([]byte(FakeRenderer))
	if len(b64) > 2 {
		return b64[:len(b64)-2]
	}
	return b64
}()

var dmImgAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

func genDmImgStr() string {
	b := make([]byte, 2)
	rand.Read(b)
	return string([]byte{dmImgAlphabet[int(b[0])%len(dmImgAlphabet)], dmImgAlphabet[int(b[1])%len(dmImgAlphabet)]})
}

// Error types
type BiliApiError struct {
	Message    string
	UID        string
	Code       int
	HTTPStatus int
}

func (e *BiliApiError) Error() string {
	var parts []string
	if e.UID != "" {
		parts = append(parts, "UID "+e.UID)
	}
	if e.HTTPStatus != 0 {
		parts = append(parts, fmt.Sprintf("HTTP %d", e.HTTPStatus))
	}
	if e.Code != 0 {
		parts = append(parts, fmt.Sprintf("code=%d", e.Code))
	}
	if e.Message != "" {
		parts = append(parts, e.Message)
	}
	if len(parts) == 0 {
		return "BiliApiError"
	}
	return strings.Join(parts, " ")
}

type BiliRiskControlError struct{ BiliApiError }
type BiliIpBlockedError struct{ BiliApiError }
type BiliCaptchaError struct{ BiliApiError }
type BiliAuthError struct{ BiliApiError }
type BiliSignError struct{ BiliApiError }
type BiliNetworkError struct{ BiliApiError }

type APIClient struct {
	cfg         *Config
	credMgr     *CredentialManager
	httpClient  *http.Client
	wbiMu       sync.Mutex
	wbiImgKey   string
	wbiSubKey   string
	wbiCachedAt time.Time
}

func newHTTPClient(cfg *Config) (*http.Client, error) {
	connectTimeout := time.Duration(cfg.HTTPTimeoutConnect * float64(time.Second))
	totalTimeout := time.Duration(cfg.HTTPTimeoutTotal * float64(time.Second))

	client, err := network.NewClient(cfg.HTTPProxy(), totalTimeout, false)
	if err != nil {
		return nil, err
	}
	if tr, ok := client.Transport.(*http.Transport); ok {
		dialer := &net.Dialer{
			Timeout:   connectTimeout,
			KeepAlive: 30 * time.Second,
		}
		tr.DialContext = dialer.DialContext
	}
	return client, nil
}

func NewAPIClient(cfg *Config, credMgr *CredentialManager) (*APIClient, error) {
	client, err := newHTTPClient(cfg)
	if err != nil {
		return nil, err
	}
	return &APIClient{
		cfg:        cfg,
		credMgr:    credMgr,
		httpClient: client,
	}, nil
}

func (c *APIClient) Close() {
	if c.httpClient != nil {
		c.httpClient.CloseIdleConnections()
	}
}

func (c *APIClient) InvalidateWbiKeys() {
	c.wbiMu.Lock()
	defer c.wbiMu.Unlock()
	c.wbiImgKey = ""
	c.wbiSubKey = ""
	c.wbiCachedAt = time.Time{}
	logging.Module("bili_dyn_sub").Info("已作废 WBI key 缓存")
}

func (c *APIClient) getWbiKeys(ctx context.Context) (string, string, error) {
	c.wbiMu.Lock()
	defer c.wbiMu.Unlock()

	if c.wbiImgKey != "" && c.wbiSubKey != "" && time.Since(c.wbiCachedAt) < time.Hour {
		return c.wbiImgKey, c.wbiSubKey, nil
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, NavURL, nil)
	if err != nil {
		return "", "", err
	}
	req.Header = c.credMgr.BuildHeaders("")
	cookies, _ := c.credMgr.GetCookies(ctx, false)
	var parts []string
	for k, v := range cookies {
		parts = append(parts, k+"="+v)
	}
	req.Header.Set("Cookie", strings.Join(parts, "; "))

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", "", fmt.Errorf("nav HTTP %d", resp.StatusCode)
	}

	var buf bytes.Buffer
	buf.ReadFrom(resp.Body)
	parsed := gjson.ParseBytes(buf.Bytes())
	imgURL := parsed.Get("data.wbi_img.img_url").String()
	subURL := parsed.Get("data.wbi_img.sub_url").String()
	imgKey := bilibili.ExtractKeyFromURL(imgURL)
	subKey := bilibili.ExtractKeyFromURL(subURL)
	if imgKey == "" || subKey == "" {
		return "", "", errors.New("empty wbi keys in nav response")
	}

	c.wbiImgKey = imgKey
	c.wbiSubKey = subKey
	c.wbiCachedAt = time.Now()
	return imgKey, subKey, nil
}

func (c *APIClient) buildFeedParams(uid, offset string) map[string]string {
	return map[string]string{
		"host_mid":         strings.TrimSpace(uid),
		"offset":           offset,
		"timezone_offset":  TimezoneOffset,
		"platform":         Platform,
		"features":         Features,
		"web_location":     WebLocation,
		"dm_img_list":      DmImgList,
		"dm_img_str":       genDmImgStr(),
		"dm_cover_img_str": dmCoverImgStr,
		"dm_img_inter":     DmImgInter,
	}
}

func (c *APIClient) FetchSpaceFeed(ctx context.Context, uid string, forceRefreshCookie bool) (gjson.Result, error) {
	uid = strings.TrimSpace(uid)
	params := c.buildFeedParams(uid, "")

	var queryStr string
	if c.cfg.EnableWBI {
		imgKey, subKey, err := c.getWbiKeys(ctx)
		if err != nil {
			logging.Module("bili_dyn_sub").WithError(err).Warn("获取 WBI key 失败，本次不签名")
			vals := url.Values{}
			for k, v := range params {
				vals.Set(k, v)
			}
			queryStr = vals.Encode()
		} else {
			now := float64(time.Now().UnixNano()) / 1e9
			wts := int64(math.RoundToEven(now))
			signed, err := bilibili.SignQuery(params, imgKey, subKey, wts)
			if err != nil {
				vals := url.Values{}
				for k, v := range params {
					vals.Set(k, v)
				}
				queryStr = vals.Encode()
			} else {
				queryStr = signed
			}
		}
	} else {
		vals := url.Values{}
		for k, v := range params {
			vals.Set(k, v)
		}
		queryStr = vals.Encode()
	}

	feedURL := FeedSpaceURL + "?" + queryStr
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, feedURL, nil)
	if err != nil {
		return gjson.Result{}, &BiliNetworkError{BiliApiError: BiliApiError{Message: err.Error(), UID: uid}}
	}
	req.Header = c.credMgr.BuildHeaders(uid)
	cookies, err := c.credMgr.GetCookies(ctx, forceRefreshCookie)
	if err == nil && len(cookies) > 0 {
		var parts []string
		for k, v := range cookies {
			parts = append(parts, k+"="+v)
		}
		req.Header.Set("Cookie", strings.Join(parts, "; "))
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return gjson.Result{}, &BiliNetworkError{BiliApiError: BiliApiError{Message: err.Error(), UID: uid}}
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusOK {
		c.credMgr.UpdateFromResponse(resp.Cookies())
	}

	if resp.StatusCode == http.StatusPreconditionFailed { // 412
		return gjson.Result{}, &BiliIpBlockedError{BiliApiError: BiliApiError{
			Message:    "IP 被 B 站风控（换 cookie 无效，需配置 proxy）",
			UID:        uid,
			HTTPStatus: 412,
		}}
	}
	if resp.StatusCode == 429 || (resp.StatusCode >= 500 && resp.StatusCode <= 504) {
		return gjson.Result{}, &BiliNetworkError{BiliApiError: BiliApiError{
			Message:    fmt.Sprintf("B 站暂时性错误响应 HTTP %d", resp.StatusCode),
			UID:        uid,
			HTTPStatus: resp.StatusCode,
		}}
	}
	if resp.StatusCode != http.StatusOK {
		return gjson.Result{}, &BiliApiError{
			Message:    fmt.Sprintf("意外的 HTTP 状态 %d", resp.StatusCode),
			UID:        uid,
			HTTPStatus: resp.StatusCode,
		}
	}

	var buf bytes.Buffer
	if _, err := io.Copy(&buf, resp.Body); err != nil {
		return gjson.Result{}, &BiliNetworkError{BiliApiError: BiliApiError{Message: err.Error(), UID: uid}}
	}

	bodyBytes := buf.Bytes()
	if !gjson.ValidBytes(bodyBytes) {
		return gjson.Result{}, &BiliApiError{Message: "响应不是合法 JSON", UID: uid, HTTPStatus: 200}
	}

	parsed := gjson.ParseBytes(bodyBytes)
	if !parsed.IsObject() {
		return gjson.Result{}, &BiliApiError{Message: "响应顶层不是对象", UID: uid, HTTPStatus: 200}
	}

	codeRes := parsed.Get("code")
	if !codeRes.Exists() || codeRes.Type != gjson.Number {
		return gjson.Result{}, &BiliApiError{Message: "响应缺少 code 字段或类型不匹配", UID: uid, HTTPStatus: 200}
	}
	code := int(codeRes.Int())
	msg := parsed.Get("message").String()
	if msg == "" {
		msg = parsed.Get("msg").String()
	}

	if code == -352 {
		return gjson.Result{}, &BiliRiskControlError{BiliApiError: BiliApiError{Message: "触发风控: " + msg, UID: uid, Code: -352, HTTPStatus: 200}}
	}
	if code == -101 {
		return gjson.Result{}, &BiliAuthError{BiliApiError: BiliApiError{Message: "登录态失效: " + msg, UID: uid, Code: -101, HTTPStatus: 200}}
	}
	if code == -403 {
		return gjson.Result{}, &BiliSignError{BiliApiError: BiliApiError{Message: "签名/权限校验失败（wbi）: " + msg, UID: uid, Code: -403, HTTPStatus: 200}}
	}
	if code != 0 {
		return gjson.Result{}, &BiliApiError{Message: "取数失败: " + msg, UID: uid, Code: code, HTTPStatus: 200}
	}

	data := parsed.Get("data")
	if !data.IsObject() {
		return gjson.Result{}, &BiliApiError{Message: "code=0 但 data 不是对象", UID: uid, Code: 0, HTTPStatus: 200}
	}

	voucher := data.Get("v_voucher").String()
	if voucher == "" {
		voucher = parsed.Get("v_voucher").String()
	}
	if voucher != "" {
		return gjson.Result{}, &BiliCaptchaError{BiliApiError: BiliApiError{
			Message:    "需要人机验证（v_voucher=" + voucher + "），本轮放弃",
			UID:        uid,
			Code:       0,
			HTTPStatus: 200,
		}}
	}

	items := data.Get("items")
	if items.Exists() && !items.IsArray() && items.Type != gjson.Null {
		return gjson.Result{}, &BiliApiError{Message: "data.items 类型异常", UID: uid, Code: 0, HTTPStatus: 200}
	}

	c.credMgr.MarkFetchSuccess()
	return data, nil
}

func (c *APIClient) FetchUserName(ctx context.Context, uid string) (string, error) {
	uid = strings.TrimSpace(uid)
	reqURL := fmt.Sprintf("%s?uid=%s", LiveUserInfoURL, uid)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, reqURL, nil)
	if err != nil {
		return "", err
	}
	req.Header = c.credMgr.BuildHeaders("")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}

	var buf bytes.Buffer
	buf.ReadFrom(resp.Body)
	parsed := gjson.ParseBytes(buf.Bytes())
	if parsed.Get("code").Int() != 0 {
		return "", fmt.Errorf("code %d", parsed.Get("code").Int())
	}
	uname := strings.TrimSpace(parsed.Get("data.info.uname").String())
	if uname == "" {
		return "", errors.New("empty uname")
	}
	return uname, nil
}
