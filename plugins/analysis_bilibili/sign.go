package analysis_bilibili

import (
	"context"
	"crypto/hmac"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

var mixinKeyEncTab = []int{
	46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
	33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
	61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
	36, 20, 34, 44, 52,
}

type signer struct {
	mu             sync.Mutex
	imgKey         string
	subKey         string
	wbiCachedAt    time.Time
	ticket         string
	ticketCachedAt time.Time
}

func newSigner() *signer {
	return &signer{}
}

func getMixinKey(orig string) string {
	if len(orig) < 64 {
		return ""
	}
	var sb strings.Builder
	for _, idx := range mixinKeyEncTab {
		if idx < len(orig) {
			sb.WriteByte(orig[idx])
		}
	}
	res := sb.String()
	if len(res) > 32 {
		return res[:32]
	}
	return res
}

func cleanWbiValue(s string) string {
	var sb strings.Builder
	for _, r := range s {
		if !strings.ContainsRune("!'()*", r) {
			sb.WriteRune(r)
		}
	}
	return sb.String()
}

func extractKeyFromURL(rawURL string) string {
	idx := strings.LastIndex(rawURL, "/")
	if idx >= 0 {
		rawURL = rawURL[idx+1:]
	}
	dot := strings.Index(rawURL, ".")
	if dot >= 0 {
		rawURL = rawURL[:dot]
	}
	return rawURL
}

func (s *signer) getWbiKeys(ctx context.Context, client *http.Client) (string, string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.imgKey != "" && s.subKey != "" && time.Since(s.wbiCachedAt) < time.Hour {
		return s.imgKey, s.subKey, nil
	}

	res, err := getJSON(ctx, client, "https://api.bilibili.com/x/web-interface/nav")
	if err != nil {
		return "", "", fmt.Errorf("nav request failed: %w", err)
	}

	imgURL := res.Get("data.wbi_img.img_url").String()
	subURL := res.Get("data.wbi_img.sub_url").String()
	imgKey := extractKeyFromURL(imgURL)
	subKey := extractKeyFromURL(subURL)
	if imgKey == "" || subKey == "" {
		return "", "", errors.New("empty wbi keys in nav response")
	}

	s.imgKey = imgKey
	s.subKey = subKey
	s.wbiCachedAt = time.Now()
	return imgKey, subKey, nil
}

func (s *signer) signQuery(ctx context.Context, client *http.Client, params map[string]string) (string, error) {
	imgKey, subKey, err := s.getWbiKeys(ctx, client)
	if err != nil {
		return "", err
	}
	mixinKey := getMixinKey(imgKey + subKey)
	if mixinKey == "" {
		return "", errors.New("invalid mixin key")
	}

	now := float64(time.Now().UnixNano()) / 1e9
	wts := int64(math.RoundToEven(now))

	vals := url.Values{}
	for k, v := range params {
		vals.Set(k, cleanWbiValue(v))
	}
	vals.Set("wts", strconv.FormatInt(wts, 10))

	query := vals.Encode()
	sum := md5.Sum([]byte(query + mixinKey))
	wRid := hex.EncodeToString(sum[:])
	return query + "&w_rid=" + wRid, nil
}

func (s *signer) getTicket(ctx context.Context, client *http.Client) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.ticket != "" && time.Since(s.ticketCachedAt) < 2*time.Hour {
		return s.ticket, nil
	}

	ts := time.Now().Unix()
	tsStr := strconv.FormatInt(ts, 10)
	mac := hmac.New(sha256.New, []byte("XgwSnGZ1p"))
	mac.Write([]byte("ts" + tsStr))
	hexSign := hex.EncodeToString(mac.Sum(nil))

	q := url.Values{}
	q.Set("key_id", "ec02")
	q.Set("hexsign", hexSign)
	q.Set("context[ts]", tsStr)
	q.Set("csrf", "")

	ticketURL := "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket?" + q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ticketURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", defaultUserAgent)
	req.Header.Set("Referer", biliReferer)
	req.Header.Set("Origin", biliOrigin)

	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusPreconditionFailed {
		return "", ErrHTTP412
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("ticket HTTP status: %d", resp.StatusCode)
	}

	body, err := readJSONBody(resp.Body)
	if err != nil {
		return "", err
	}

	if body.Get("code").Int() == -412 {
		return "", ErrHTTP412
	}

	ticket := body.Get("data.ticket").String()
	if ticket == "" {
		return "", errors.New("ticket empty in response")
	}

	s.ticket = ticket
	s.ticketCachedAt = time.Now()
	return ticket, nil
}

func setTicketCookie(client *http.Client, ticket string) {
	if client.Jar == nil || ticket == "" {
		return
	}
	biliURL, _ := url.Parse("https://api.bilibili.com")
	client.Jar.SetCookies(biliURL, []*http.Cookie{
		{
			Name:   "bili_ticket",
			Value:  ticket,
			Domain: ".bilibili.com",
			Path:   "/",
		},
	})
}

func (s *signer) ensureTicket(ctx context.Context, client *http.Client) {
	ticket, err := s.getTicket(ctx, client)
	if err != nil {
		logging.Module("analysis_bilibili").Debugf("analysis_bilibili get_ticket failed: %v", err)
		return
	}
	setTicketCookie(client, ticket)
}
