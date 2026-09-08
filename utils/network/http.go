package network

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/HakuchumuHYX/HakuBot/utils"
)

type HTTPError struct{ Status int }

func (e *HTTPError) Error() string { return fmt.Sprintf("HTTP status %d", e.Status) }
func Retryable(err error) bool {
	var e *HTTPError
	if errors.As(err, &e) {
		switch e.Status {
		case 408, 429, 500, 502, 503, 504:
			return true
		}
		return false
	}
	var networkError net.Error
	return !errors.Is(err, context.Canceled) && (errors.As(err, &networkError) || errors.Is(err, io.ErrUnexpectedEOF))
}

var ErrTooLarge = errors.New("download exceeds size limit")

// Empty proxy inherits environment; "direct" explicitly bypasses proxies.
func NewClient(proxy string, timeout time.Duration, cookies bool) (*http.Client, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if proxy == "direct" {
		transport.Proxy = nil
	} else if proxy != "" {
		for strings.HasPrefix(proxy, "http://http://") || strings.HasPrefix(proxy, "https://https://") {
			proxy = strings.SplitN(proxy, "://", 2)[1]
		}
		if !strings.Contains(proxy, "://") {
			proxy = "http://" + proxy
		}
		u, err := url.Parse(proxy)
		if err != nil {
			return nil, err
		}
		transport.Proxy = http.ProxyURL(u)
	}
	if timeout == 0 {
		timeout = 120 * time.Second
	}
	c := &http.Client{Transport: transport, Timeout: timeout}
	if cookies {
		jar, err := cookiejar.New(nil)
		if err != nil {
			return nil, err
		}
		c.Jar = jar
	}
	return c, nil
}

type Sessions struct {
	mu      sync.Mutex
	clients map[string]*http.Client
	closed  bool
}

func (s *Sessions) Get(namespace, proxy string) (*http.Client, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return nil, errors.New("HTTP sessions closed")
	}
	key := namespace + "\x00" + proxy
	if c := s.clients[key]; c != nil {
		return c, nil
	}
	c, err := NewClient(proxy, 0, namespace != "public")
	if err != nil {
		return nil, err
	}
	if s.clients == nil {
		s.clients = map[string]*http.Client{}
	}
	s.clients[key] = c
	return c, nil
}
func (s *Sessions) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	for _, c := range s.clients {
		c.CloseIdleConnections()
	}
	return nil
}

type DownloadOptions struct {
	Headers  http.Header
	MaxBytes int64
	Attempts int
}

func (o DownloadOptions) defaults() DownloadOptions {
	if o.MaxBytes == 0 {
		o.MaxBytes = 20 << 20
	}
	if o.Attempts == 0 {
		o.Attempts = 3
	}
	return o
}
func Wait(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
func Download(ctx context.Context, client *http.Client, address string, options DownloadOptions) ([]byte, error) {
	var data []byte
	err := download(ctx, client, address, options, func(r io.Reader) error { var err error; data, err = io.ReadAll(r); return err })
	return data, err
}
func DownloadFile(ctx context.Context, client *http.Client, address, path string, options DownloadOptions) error {
	return download(ctx, client, address, options, func(r io.Reader) error {
		return utils.AtomicFile(path, func(w io.Writer) error { _, err := io.Copy(w, r); return err })
	})
}
func download(ctx context.Context, c *http.Client, address string, o DownloadOptions, consume func(io.Reader) error) error {
	o = o.defaults()
	if o.MaxBytes <= 0 || o.Attempts < 1 {
		return errors.New("invalid download limits")
	}
	for attempt := 0; attempt < o.Attempts; attempt++ {
		err := func() error {
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
			if err != nil {
				return err
			}
			req.Header = o.Headers.Clone()
			rsp, err := c.Do(req)
			if err != nil {
				return err
			}
			defer rsp.Body.Close()
			if rsp.StatusCode != http.StatusOK {
				return &HTTPError{rsp.StatusCode}
			}
			if rsp.ContentLength > o.MaxBytes {
				return ErrTooLarge
			}
			return consume(&limitedReader{r: rsp.Body, left: o.MaxBytes})
		}()
		if err == nil {
			return nil
		}
		if attempt == o.Attempts-1 || !Retryable(err) {
			return err
		}
		if err = Wait(ctx, time.Second*time.Duration(1<<min(attempt, 5))); err != nil {
			return err
		}
	}
	return nil
}

type limitedReader struct {
	r    io.Reader
	left int64
}

func (l *limitedReader) Read(p []byte) (int, error) {
	if int64(len(p)) > l.left+1 {
		p = p[:l.left+1]
	}
	n, err := l.r.Read(p)
	l.left -= int64(n)
	if l.left < 0 {
		return 0, ErrTooLarge
	}
	return n, err
}
func TemporaryDownload(ctx context.Context, c *http.Client, address, suffix string, o DownloadOptions) (string, func(), error) {
	f, err := os.CreateTemp("", "hakubot-download-*"+suffix)
	if err != nil {
		return "", nil, err
	}
	name := f.Name()
	f.Close()
	cleanup := func() { os.Remove(name) }
	if err = DownloadFile(ctx, c, address, name, o); err != nil {
		cleanup()
		return "", nil, err
	}
	return name, cleanup, nil
}
