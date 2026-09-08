package rendering

import (
	"context"
	"errors"
	"html"
	"net/url"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

type Browser struct {
	mu                      sync.Mutex
	ctx                     context.Context
	cancel, allocatorCancel context.CancelFunc
	slots                   chan struct{}
	executable              string
	closed                  bool
}

// NewBrowser owns a lazily started Chromium; no browser or resources are installed automatically.
func NewBrowser(executable string) *Browser {
	return &Browser{executable: executable, slots: make(chan struct{}, 5)}
}
func (b *Browser) tab(ctx context.Context) (context.Context, func(), error) {
	select {
	case b.slots <- struct{}{}:
	case <-ctx.Done():
		return nil, nil, ctx.Err()
	}
	release := func() { <-b.slots }
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		release()
		return nil, nil, errors.New("browser closed")
	}
	if b.ctx == nil {
		options := append([]chromedp.ExecAllocatorOption(nil), chromedp.DefaultExecAllocatorOptions[:]...)
		if b.executable != "" {
			options = append(options, chromedp.ExecPath(b.executable))
		}
		alloc, ac := chromedp.NewExecAllocator(context.Background(), options...)
		root, rc := chromedp.NewContext(alloc)
		stopStartup := context.AfterFunc(ctx, rc)
		err := chromedp.Run(root)
		stopStartup()
		if err == nil {
			err = ctx.Err()
		}
		if err != nil {
			rc()
			ac()
			release()
			return nil, nil, err
		}
		b.ctx = root
		b.cancel = rc
		b.allocatorCancel = ac
	}
	tab, cancel := chromedp.NewContext(b.ctx, chromedp.WithNewBrowserContext())
	stop := context.AfterFunc(ctx, cancel)
	return tab, func() { stop(); cancel(); release() }, nil
}
func (b *Browser) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.closed = true
	if b.cancel != nil {
		b.cancel()
		b.allocatorCancel()
	}
	return nil
}

type Options struct {
	Width, Height int
	Scale         float64
	Timeout       time.Duration
	BaseDir       string
	Quality       int
}

func (o Options) defaults() Options {
	if o.Width <= 0 {
		o.Width = 500
	}
	if o.Height <= 0 {
		o.Height = 100
	}
	if o.Scale <= 0 {
		o.Scale = 2
	}
	if o.Timeout <= 0 {
		o.Timeout = 30 * time.Second
	}
	return o
}
func (b *Browser) page(ctx context.Context, content string, o Options, actions ...chromedp.Action) error {
	o = o.defaults()
	ctx, timeout := context.WithTimeout(ctx, o.Timeout)
	defer timeout()
	tab, close, err := b.tab(ctx)
	if err != nil {
		return err
	}
	defer close()
	navigate := "about:blank"
	if o.BaseDir != "" {
		absolute, err := filepath.Abs(o.BaseDir)
		if err != nil {
			return err
		}
		navigate = (&url.URL{Scheme: "file", Path: absolute + "/"}).String()
		base := `<base href="` + html.EscapeString(navigate) + `">`
		if strings.Contains(content, "<head>") {
			content = strings.Replace(content, "<head>", "<head>"+base, 1)
		} else {
			content = base + content
		}
	}
	ready := `(async()=>{await document.fonts.ready;await Promise.all(Array.from(document.images).map(i=>i.decode().catch(()=>{})));return true})()`
	tasks := []chromedp.Action{chromedp.EmulateViewport(int64(o.Width), int64(o.Height), chromedp.EmulateScale(o.Scale)), chromedp.Navigate(navigate), chromedp.ActionFunc(func(ctx context.Context) error {
		return chromedp.Evaluate(`document.open();document.write(`+quote(content)+`);document.close();`, nil).Do(ctx)
	}), chromedp.Evaluate(ready, nil, func(p *runtime.EvaluateParams) *runtime.EvaluateParams { return p.WithAwaitPromise(true) })}
	return chromedp.Run(tab, append(tasks, actions...)...)
}
func (b *Browser) HTML(ctx context.Context, content string, o Options) ([]byte, error) {
	var data []byte
	err := b.page(ctx, content, o, chromedp.FullScreenshot(&data, o.Quality))
	return data, err
}
