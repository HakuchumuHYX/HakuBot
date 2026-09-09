package analysis_bilibili

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"

	"github.com/HakuchumuHYX/HakuBot/utils/network"
	"github.com/tidwall/gjson"
)

const (
	defaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
	biliReferer      = "https://www.bilibili.com"
	biliOrigin       = "https://www.bilibili.com"
	maxJSONBytes     = 8 << 20  // 8 MiB
	maxImageBytes    = 20 << 20 // 20 MiB
)

var ErrHTTP412 = errors.New("HTTP 412")

func readJSONBody(r io.Reader) (gjson.Result, error) {
	limited := io.LimitReader(r, maxJSONBytes)
	body, err := io.ReadAll(limited)
	if err != nil {
		return gjson.Result{}, fmt.Errorf("读取响应失败: %w", err)
	}
	if !gjson.ValidBytes(body) {
		return gjson.Result{}, errors.New("响应不是合法 JSON")
	}
	res := gjson.ParseBytes(body)
	if !res.IsObject() {
		return gjson.Result{}, errors.New("JSON 响应不是对象")
	}
	return res, nil
}

func getJSON(ctx context.Context, client *http.Client, rawURL string) (gjson.Result, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return gjson.Result{}, err
	}
	req.Header.Set("User-Agent", defaultUserAgent)
	req.Header.Set("Referer", biliReferer)
	req.Header.Set("Origin", biliOrigin)

	resp, err := client.Do(req)
	if err != nil {
		return gjson.Result{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusPreconditionFailed {
		return gjson.Result{}, ErrHTTP412
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return gjson.Result{}, fmt.Errorf("HTTP 状态码: %d", resp.StatusCode)
	}

	res, err := readJSONBody(resp.Body)
	if err != nil {
		return gjson.Result{}, err
	}
	if res.Get("code").Int() == -412 {
		return gjson.Result{}, ErrHTTP412
	}
	return res, nil
}

func searchVideoByTitle(ctx context.Context, client *http.Client, s *signer, title string) (string, error) {
	ticket, err := s.getTicket(ctx, client)
	if err != nil {
		return "", fmt.Errorf("获取 ticket 失败: %w", err)
	}
	setTicketCookie(client, ticket)

	reqMain, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://www.bilibili.com", nil)
	if err != nil {
		return "", err
	}
	reqMain.Header.Set("User-Agent", defaultUserAgent)
	reqMain.Header.Set("Referer", biliReferer)
	respMain, err := client.Do(reqMain)
	if err != nil {
		return "", err
	}
	respMain.Body.Close()
	if respMain.StatusCode == http.StatusPreconditionFailed {
		return "", ErrHTTP412
	}
	if respMain.StatusCode != http.StatusOK {
		return "", fmt.Errorf("主站请求失败: HTTP %d", respMain.StatusCode)
	}

	query, err := s.signQuery(ctx, client, map[string]string{"keyword": title})
	if err != nil {
		return "", fmt.Errorf("WBI 签名失败: %w", err)
	}

	searchURL := "https://api.bilibili.com/x/web-interface/wbi/search/all/v2?" + query
	res, err := getJSON(ctx, client, searchURL)
	if err != nil {
		return "", err
	}

	if res.Get("code").Int() == -412 {
		return "", ErrHTTP412
	}
	if res.Get("code").Int() != 0 {
		return "", fmt.Errorf("搜索接口返回错误码: %d", res.Get("code").Int())
	}

	data := res.Get("data")
	for _, item := range data.Get("result").Array() {
		if item.Get("result_type").String() == "video" {
			videos := item.Get("data").Array()
			if len(videos) == 0 {
				videos = item.Get("result").Array()
			}
			if len(videos) > 0 {
				arcURL := videos[0].Get("arcurl").String()
				if arcURL != "" {
					return arcURL, nil
				}
			}
			break
		}
	}
	return "", errors.New("未找到视频")
}

func downloadImage(ctx context.Context, client *http.Client, imgURL string) ([]byte, error) {
	if imgURL == "" {
		return nil, errors.New("empty image url")
	}
	headers := make(http.Header)
	headers.Set("User-Agent", defaultUserAgent)
	headers.Set("Referer", biliReferer)
	downloaded, err := network.FetchImage(ctx, client, imgURL, network.DownloadOptions{
		Headers:  headers,
		MaxBytes: maxImageBytes,
		Attempts: 1,
	})
	if err != nil {
		return nil, err
	}
	return downloaded.Data, nil
}
