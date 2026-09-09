package analysis_bilibili

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils/bilibili"
	"github.com/HakuchumuHYX/HakuBot/utils/logging"
)

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
	imgKey := bilibili.ExtractKeyFromURL(imgURL)
	subKey := bilibili.ExtractKeyFromURL(subURL)
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
	now := float64(time.Now().UnixNano()) / 1e9
	wts := int64(math.RoundToEven(now))
	return bilibili.SignQuery(params, imgKey, subKey, wts)
}

func (s *signer) getTicket(ctx context.Context, client *http.Client) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.ticket != "" && time.Since(s.ticketCachedAt) < 2*time.Hour {
		return s.ticket, nil
	}

	q := bilibili.BuildTicketParams(time.Now().Unix())
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
