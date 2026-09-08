package rendering

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"html/template"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/HakuchumuHYX/HakuBot/utils"
	"github.com/HakuchumuHYX/HakuBot/utils/images"
	"github.com/chromedp/chromedp"
)

type HelpEntry struct {
	Command, Description, Arguments, Example, Permission string
	Aliases                                              []string
}
type HelpSection struct {
	Title, Note string
	Entries     []HelpEntry
}
type HelpDocument struct {
	Title, Introduction, Footer string
	Sections                    []HelpSection
	Paragraphs, Tips, Links     []string
}
type Page struct {
	Data          []byte
	Width, Height int
}
type Document struct{ Pages []Page }

func HelpText(d HelpDocument) string {
	blocks := []string{d.Title}
	if d.Introduction != "" {
		blocks = append(blocks, d.Introduction)
	}
	blocks = append(blocks, d.Paragraphs...)
	for _, s := range d.Sections {
		blocks = append(blocks, "【"+s.Title+"】\n"+s.Note)
		for _, e := range s.Entries {
			title, text := entryText(e)
			blocks = append(blocks, title+"\n"+text)
		}
	}
	blocks = append(blocks, d.Tips...)
	blocks = append(blocks, d.Links...)
	if d.Footer != "" {
		blocks = append(blocks, d.Footer)
	}
	return strings.Join(blocks, "\n\n")
}
func entryText(e HelpEntry) (string, string) {
	title := strings.TrimSpace(e.Command + " " + e.Arguments)
	if e.Permission != "" {
		title += " [" + e.Permission + "]"
	}
	text := e.Description
	if len(e.Aliases) > 0 {
		text += "\n别名：" + strings.Join(e.Aliases, "、")
	}
	if e.Example != "" {
		text += "\n示例：" + e.Example
	}
	return title, text
}
func ThemeCSS(mode string, now time.Time) (string, error) {
	if mode == "auto" || mode == "" {
		tz, err := time.LoadLocation("Asia/Shanghai")
		if err != nil {
			return "", err
		}
		hour := now.In(tz).Hour()
		mode = "dark"
		if hour >= 6 && hour < 18 {
			mode = "light"
		}
	}
	if mode == "light" {
		return "--canvas-bg:rgb(228,245,255);--card-bg:rgb(243,251,255);--card-border:rgb(170,210,235);--text-main:rgb(25,55,75);--text-sub:rgb(80,120,140);--text-muted:rgb(120,150,165);--accent:rgb(35,125,175);", nil
	}
	if mode == "dark" {
		return "--canvas-bg:rgb(30,32,40);--card-bg:rgb(40,44,55);--card-border:rgb(60,65,80);--text-main:rgb(220,225,235);--text-sub:rgb(160,170,185);--text-muted:rgb(110,120,135);--accent:rgb(120,230,210);", nil
	}
	return "", fmt.Errorf("invalid theme %q", mode)
}
func (b *Browser) Help(ctx context.Context, paths utils.Paths, cache *Cache, d HelpDocument, theme string, force bool) (Document, error) {
	css, err := ThemeCSS(theme, time.Now())
	if err != nil {
		return Document{}, err
	}
	font, err := FontCSS(paths)
	if err != nil {
		return Document{}, err
	}
	type unit struct{ Title, Text string }
	var units []unit
	add := func(title, text string) {
		r := []rune(text)
		if len(r) == 0 {
			units = append(units, unit{title, ""})
		}
		for i := 0; i < len(r); i += 600 {
			t := title
			if i > 0 && t != "" {
				t += "（续）"
			}
			units = append(units, unit{t, string(r[i:min(i+600, len(r))])})
		}
	}
	for _, p := range d.Paragraphs {
		add("", p)
	}
	for _, s := range d.Sections {
		units = append(units, unit{s.Title, s.Note})
		for _, e := range s.Entries {
			title, text := entryText(e)
			add(title, text)
		}
	}
	for _, s := range append(append([]string{}, d.Tips...), d.Links...) {
		add("", s)
	}
	t, err := template.ParseFS(templates, "templates/help.html")
	if err != nil {
		return Document{}, err
	}
	var out bytes.Buffer
	err = t.Execute(&out, struct {
		Document          HelpDocument
		Units             []unit
		FontCSS, ThemeCSS template.CSS
	}{d, units, template.CSS(font), template.CSS(css)})
	if err != nil {
		return Document{}, err
	}
	digest := sha256.Sum256(out.Bytes())
	key := hex.EncodeToString(digest[:])
	render := func() (Document, error) {
		var document Document
		var count int
		err := b.page(ctx, out.String(), Options{Width: 800, Scale: 2}, chromedp.Evaluate("window.paginateHelp()", nil), chromedp.Evaluate("document.querySelectorAll('.sheet').length", &count), chromedp.ActionFunc(func(ctx context.Context) error {
			for i := 0; i < count; i++ {
				var data []byte
				selector := fmt.Sprintf("article.sheet:nth-of-type(%d)", i+1)
				if err := chromedp.Screenshot(selector, &data, chromedp.ByQuery).Do(ctx); err != nil {
					return err
				}
				img, _, err := images.Decode(data)
				if err != nil {
					return err
				}
				if img.Bounds().Dy() > 4800 {
					return fmt.Errorf("help page exceeds height limit")
				}
				document.Pages = append(document.Pages, Page{data, img.Bounds().Dx(), img.Bounds().Dy()})
			}
			return nil
		}))
		return document, err
	}
	if cache == nil {
		return render()
	}
	return cache.Render(ctx, key, force, render)
}
